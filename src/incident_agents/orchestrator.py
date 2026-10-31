"""Supervisor/specialist orchestration with handoffs, budgets and human approval.

Pattern: the supervisor sees only short specialist summaries (the *case file*) and
routes work with ``transfer_to_<agent>`` tools. Each specialist runs its own bounded
tool loop with an allow-list of tools. Side-effecting tools pass through an
approval callback (human in the loop). Every step is traced.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from .agents import SUPERVISOR_INSTRUCTIONS, Agent
from .llm import ChatModel
from .tools import Tool
from .tracing import tracer

Approver = Callable[[str, str, dict], bool]  # (agent, tool, arguments) -> approved?


def deny_all(agent: str, tool: str, args: dict) -> bool:
    return False


def approve_all(agent: str, tool: str, args: dict) -> bool:
    return True


@dataclass
class RunResult:
    status: str  # completed | budget_exceeded | max_handoffs
    report: str
    handoffs: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)
    case_file: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class BudgetExceeded(RuntimeError):
    pass


class Orchestrator:
    def __init__(
        self,
        model: ChatModel,
        specialists: list[Agent],
        reporter: Agent,
        approver: Approver = deny_all,
        max_handoffs: int = 6,
        token_budget: int = 60_000,
    ):
        self.model = model
        self.specialists = {a.name: a for a in specialists}
        self.reporter = reporter
        self.approver = approver
        self.max_handoffs = max_handoffs
        self.token_budget = token_budget

    # ------------------------------------------------------------------ helpers
    def _chat(self, agent: str, messages: list[dict], tools: list[dict] | None, result: RunResult):
        with tracer().start_as_current_span(f"chat {self.model.model}") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", self.model.model)
            span.set_attribute("gen_ai.agent.name", agent)
            out = self.model.chat(messages, tools)
            span.set_attribute("gen_ai.usage.input_tokens", out.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", out.output_tokens)
        result.input_tokens += out.input_tokens
        result.output_tokens += out.output_tokens
        if result.input_tokens + result.output_tokens > self.token_budget:
            raise BudgetExceeded
        return out

    def _supervisor_tools(self) -> list[dict]:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": f"transfer_to_{a.name}",
                    "description": f"Delegate a task to {a.name}: {a.description}",
                    "parameters": {
                        "type": "object",
                        "required": ["task"],
                        "properties": {"task": {"type": "string"}},
                    },
                },
            }
            for a in self.specialists.values()
        ]
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": "The case file is complete; write the report.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        )
        return tools

    @staticmethod
    def _case_text(case: list[dict]) -> str:
        if not case:
            return "(empty)"
        return "\n\n".join(f"### {c['agent']}\n{c['findings']}" for c in case)

    # ------------------------------------------------------------------ agents
    def _run_agent(self, agent: Agent, incident: str, task: str, result: RunResult) -> str:
        tools = {t.name: t for t in agent.tools}
        messages = [
            {"role": "system", "content": agent.system_prompt()},
            {
                "role": "user",
                "content": f"INCIDENT:\n{incident}\n\nCASE FILE:\n"
                f"{self._case_text(result.case_file)}\n\nTASK: {task}",
            },
        ]
        with tracer().start_as_current_span(f"invoke_agent {agent.name}") as span:
            span.set_attribute("gen_ai.operation.name", "invoke_agent")
            span.set_attribute("gen_ai.agent.name", agent.name)
            for _ in range(agent.max_steps):
                out = self._chat(
                    agent.name, messages, [t.schema() for t in tools.values()] or None, result
                )
                if not out.tool_calls:
                    return out.content or ""
                messages.append(
                    {
                        "role": "assistant",
                        "content": out.content,
                        "tool_calls": [
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                            }
                            for c in out.tool_calls
                        ],
                    }
                )
                for call in out.tool_calls:
                    content = self._execute(
                        agent, tools.get(call.name), call.name, call.arguments, result
                    )
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
            return "Step limit reached before a conclusion."

    def _execute(
        self, agent: Agent, tool: Tool | None, name: str, args: dict, result: RunResult
    ) -> str:
        with tracer().start_as_current_span(f"execute_tool {name}") as span:
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", name)
            span.set_attribute("gen_ai.agent.name", agent.name)
            if tool is None:  # least privilege: agents can only call their own tools
                content = json.dumps({"error": f"tool '{name}' is not available to {agent.name}"})
            elif tool.requires_approval:
                approved = self.approver(agent.name, name, args)
                result.approvals.append(
                    {"agent": agent.name, "tool": name, "arguments": args, "approved": approved}
                )
                span.set_attribute("approval.granted", approved)
                content = (
                    tool.invoke(args)
                    if approved
                    else json.dumps(
                        {
                            "status": "rejected_by_human",
                            "message": "A human rejected this action. Do not retry; report it.",
                        }
                    )
                )
            else:
                content = tool.invoke(args)
        result.tool_calls.append(
            {"agent": agent.name, "tool": name, "arguments": args, "result": json.loads(content)}
        )
        return content

    # ------------------------------------------------------------------ main loop
    def run(self, incident: str) -> RunResult:
        result = RunResult(status="completed", report="")
        start = time.perf_counter()
        supervisor = Agent("supervisor", "", SUPERVISOR_INSTRUCTIONS)
        messages = [
            {"role": "system", "content": supervisor.system_prompt()},
            {"role": "user", "content": f"INCIDENT:\n{incident}"},
        ]
        with tracer().start_as_current_span("invoke_agent supervisor") as root:
            root.set_attribute("gen_ai.operation.name", "invoke_agent")
            root.set_attribute("gen_ai.agent.name", "supervisor")
            try:
                done = False
                while not done:
                    if len(result.handoffs) >= self.max_handoffs:
                        result.status = "max_handoffs"
                        break
                    out = self._chat("supervisor", messages, self._supervisor_tools(), result)
                    if not out.tool_calls:
                        break
                    messages.append(
                        {
                            "role": "assistant",
                            "content": out.content,
                            "tool_calls": [
                                {
                                    "id": c.id,
                                    "type": "function",
                                    "function": {
                                        "name": c.name,
                                        "arguments": json.dumps(c.arguments),
                                    },
                                }
                                for c in out.tool_calls
                            ],
                        }
                    )
                    for call in out.tool_calls:
                        target = call.name.removeprefix("transfer_to_")
                        if call.name == "finish":
                            done = True
                            reply = "ok"
                        elif target in self.specialists:
                            result.handoffs.append(target)
                            findings = self._run_agent(
                                self.specialists[target],
                                incident,
                                call.arguments.get("task", ""),
                                result,
                            )
                            result.case_file.append({"agent": target, "findings": findings})
                            reply = findings
                        else:
                            reply = f"unknown tool {call.name}"
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": reply})
                result.report = self._run_agent(
                    self.reporter, incident, "Write the incident report.", result
                )
            except BudgetExceeded:
                result.status = "budget_exceeded"
                result.report = "Token budget exhausted. Partial case file:\n\n" + self._case_text(
                    result.case_file
                )
            root.set_attribute("incident.status", result.status)
        result.seconds = round(time.perf_counter() - start, 3)
        return result
