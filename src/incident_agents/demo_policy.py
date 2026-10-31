"""Deterministic, rule-based stand-in for the chat model (offline demo, CI, tests).

It plays each role the way a well-behaved model is expected to: the supervisor
delegates in order and stops when the case is clear, specialists call their tools
and summarise *only* what the tools returned. This makes the orchestration, tool
contracts, approval flow and tracing runnable without Azure credentials. It is not
a substitute for evaluating a real model: use ``--backend azure`` for that.
"""

from __future__ import annotations

import json
import re

from .llm import ChatModel, ChatResult, ToolCall

_ASSET = re.compile(r"\b[A-Z]-\d{3}\b")


def _tokens(messages: list[dict]) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages) // 4


class DemoPolicy(ChatModel):
    model = "demo-policy"

    def __init__(self) -> None:
        self._n = 0

    def _call(self, name: str, **args) -> ToolCall:
        self._n += 1
        return ToolCall(f"call_{self._n}", name, args)

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        role = messages[0]["content"].split("\n", 1)[0].removeprefix("ROLE: ")
        user = messages[1]["content"]
        asset = (_ASSET.findall(user) or ["P-101"])[0]
        results = [
            json.loads(m["content"])
            for m in messages
            if m["role"] == "tool" and m["content"].startswith(("{", "["))
        ]
        out = getattr(self, f"_{role}")(asset, user, messages, results)
        if isinstance(out, list):
            return ChatResult(None, out, _tokens(messages), 30 * len(out))
        return ChatResult(out, [], _tokens(messages), len(out) // 4)

    # --------------------------------------------------------------- supervisor
    def _supervisor(self, asset, user, messages, results):
        done = [m for m in messages if m["role"] == "tool"]
        last = done[-1]["content"] if done else ""
        if len(done) == 1 and "no significant anomaly" in last:
            return [self._call("finish")]
        plan = [
            (
                "transfer_to_telemetry_analyst",
                f"Quantify the anomalous sensors of {asset} over the last 72 h.",
            ),
            (
                "transfer_to_maintenance_expert",
                f"Diagnose the probable root cause for {asset} from the telemetry evidence.",
            ),
            (
                "transfer_to_maintenance_planner",
                f"Check parts and create a work order for {asset} with the right priority.",
            ),
        ]
        if len(done) < len(plan):
            name, task = plan[len(done)]
            return [self._call(name, task=task)]
        return [self._call("finish")]

    # --------------------------------------------------------------- telemetry
    def _telemetry_analyst(self, asset, user, messages, results):
        if not results:
            return [self._call("get_anomaly_events", asset_id=asset)]
        events = results[0]
        if not events:
            return f"{asset}: no significant anomaly in the last 72 h."
        if len(results) == 1:
            sensors = [e["sensor"] for e in events] + ["bearing_temp"]
            return [
                self._call("get_sensor_stats", asset_id=asset, sensor=s)
                for s in dict.fromkeys(sensors)
            ]
        lines = [f"Anomalous signals on {asset} (72 h):"]
        evidence = {}
        for st in results[1:]:
            lines.append(
                f"- {st['sensor']}: {st['baseline']} -> {st['current']} "
                f"(change {st['change']:+}, trend {st['trend_per_day']:+}/day)"
            )
            evidence[st["sensor"]] = {"change": st["change"], "current": st["current"]}
        lines.append("Evidence: " + json.dumps(evidence))
        return "\n".join(lines)

    # --------------------------------------------------------------- diagnosis
    def _maintenance_expert(self, asset, user, messages, results):
        m = re.search(r"Evidence: (\{.*\})", user)
        ev = json.loads(m.group(1)) if m else {}
        vib = ev.get("vibration_rms", {})
        temp = ev.get("bearing_temp", {}).get("change", 0.0)
        flow = ev.get("flow", {}).get("change", 0.0)
        if not results:
            if flow < -10:
                query = "noisy fluctuating discharge pressure drop in flow"
            else:
                query = "high vibration bearing wear" if temp >= 8 else "high vibration"
            return [
                self._call("search_manual", query=query),
                self._call("get_maintenance_history", asset_id=asset),
            ]
        docs = results[0] if isinstance(results[0], list) else []
        history = results[1] if len(results) > 1 else []
        doc = docs[0]["doc"] if docs else "none"
        v = vib.get("current", 0.0)
        zone = (
            "D (stop within 24 h)" if v > 7.1 else "C (restricted operation)" if v > 4.5 else ("B")
        )
        if flow < -10:
            cause, parts, downtime = (
                "cavitation (suction side)",
                "none (clean strainer STR-80)",
                "1 h",
            )
        elif temp >= 8:
            cause, parts, downtime = "drive-end bearing wear", "BRG-6310", "4 h"
        else:
            cause, parts, downtime = "impeller imbalance from deposits", "none", "3 h"
        last = history[0]["date"] + " " + history[0]["work"] if history else "no history"
        return (
            f"Probable cause: {cause}. Vibration {v} mm/s, severity zone {zone}; "
            f"bearing temperature change {temp:+} K. Parts: {parts}. Downtime: {downtime}. "
            f"Last maintenance: {last}. Source: manual '{doc}'."
        )

    # --------------------------------------------------------------- planning
    def _maintenance_planner(self, asset, user, messages, results):
        part = re.search(r"Parts: ([A-Z]+-\d+)", user)
        part_no = part.group(1) if part else None
        zone_c = "zone C" in user or "zone D" in user
        priority = "P1" if "zone D" in user else "P2" if zone_c else "P3"
        cause = re.search(r"Probable cause: ([^.]+)\.", user)
        title = f"{cause.group(1).capitalize() if cause else 'Inspection'} on {asset}"
        if not results:
            if part_no:
                return [self._call("check_spare_parts", part_no=part_no)]
            return [
                self._call(
                    "create_work_order", asset_id=asset, title=title, priority=priority, parts=""
                )
            ]
        last = results[-1]
        if "in_stock" in last and len(results) == 1:
            return [
                self._call(
                    "create_work_order",
                    asset_id=asset,
                    title=title,
                    priority=priority,
                    parts=part_no or "",
                )
            ]
        stock = next((r for r in results if "in_stock" in r), None)
        stock_txt = (
            f"{stock['part_no']}: {stock['in_stock']} in stock" if stock else "no parts needed"
        )
        if last.get("status") == "rejected_by_human":
            return (
                f"Work order NOT created: rejected by the approver. Proposed: '{title}', "
                f"priority {priority}. {stock_txt}."
            )
        return f"Created {last['id']} '{title}' with priority {priority}. {stock_txt}."

    # --------------------------------------------------------------- report
    def _reporter(self, asset, user, messages, results):
        case = user.split("CASE FILE:", 1)[1].split("TASK:", 1)[0].strip()
        sections = dict(re.findall(r"### (\w+)\n(.*?)(?=\n### |\Z)", case, re.S))
        telemetry = sections.get("telemetry_analyst", "n/a")
        telemetry = re.sub(r"\nEvidence: .*", "", telemetry)
        diag = sections.get("maintenance_expert", "No diagnosis performed.")
        plan = sections.get("maintenance_planner", "No action planned.")
        summary = diag.split(".")[0] if "Probable cause" in diag else telemetry.split("\n")[0]
        open_points = (
            "Approval pending: work order was rejected."
            if "NOT created" in plan
            else "Verify vibration returns below 2.8 mm/s after the repair."
        )
        return (
            f"## Incident report: {asset}\n\n**Summary:** {summary}.\n\n"
            f"**Evidence**\n{telemetry}\n\n**Diagnosis**\n{diag}\n\n"
            f"**Actions**\n{plan}\n\n**Open points**\n{open_points}"
        )
