"""incident-agents: run a triage, or evaluate the scenario suite."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agents import build_team
from .demo_policy import DemoPolicy
from .evaluation import run_suite
from .orchestrator import Orchestrator, approve_all, deny_all
from .plant import Plant
from .tracing import configure


def make_model(backend: str):
    if backend == "demo":
        return DemoPolicy()
    if backend == "azure":
        from .llm import AzureOpenAIChat

        return AzureOpenAIChat(
            os.environ["AZURE_OPENAI_ENDPOINT"],
            os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),
            api_key=os.environ.get("AZURE_OPENAI_API_KEY"),
        )
    if backend == "openai":
        from .llm import OpenAICompatibleChat

        return OpenAICompatibleChat(
            os.environ["OPENAI_BASE_URL"],
            os.environ["OPENAI_MODEL"],
            os.environ.get("OPENAI_API_KEY"),
        )
    raise ValueError(backend)


def _interactive(agent: str, tool: str, args: dict) -> bool:
    print(f"\n[approval] {agent} wants to call {tool}({json.dumps(args)})")
    return input("approve? [y/N] ").strip().lower() == "y"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="incident-agents")
    p.add_argument("--backend", choices=["demo", "azure", "openai"], default="demo")
    p.add_argument("--trace", action="store_true", help="print OpenTelemetry spans")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--asset", default="P-101")
    r.add_argument(
        "--mode",
        default="bearing_wear",
        choices=["bearing_wear", "imbalance", "cavitation", "healthy"],
    )
    r.add_argument("--approve", choices=["ask", "yes", "no"], default="ask")
    r.add_argument("--json", action="store_true")
    e = sub.add_parser("eval")
    e.add_argument("--scenarios", default="scenarios")
    e.add_argument("--out", default="artifacts/eval")
    args = p.parse_args(argv)
    configure(console=args.trace)

    if args.cmd == "run":
        plant = Plant(assets={args.asset: args.mode})
        specialists, reporter = build_team(plant)
        approver = {"ask": _interactive, "yes": approve_all, "no": deny_all}[args.approve]
        orch = Orchestrator(make_model(args.backend), specialists, reporter, approver)
        res = orch.run(
            f"Anomaly alert from the condition-monitoring service for pump "
            f"{args.asset}: vibration above alarm limit."
        )
        print(res.report)
        print(
            f"\n-- status={res.status} handoffs={' -> '.join(res.handoffs)} "
            f"tool_calls={len(res.tool_calls)} approvals={len(res.approvals)} "
            f"tokens={res.input_tokens + res.output_tokens} time={res.seconds}s"
        )
        if args.json:
            print(json.dumps(res.to_dict(), indent=2, default=str))
    else:
        from datetime import date

        model = make_model(args.backend).model
        out = Path(args.out)
        report = run_suite(
            Path(args.scenarios), lambda: make_model(args.backend), transcripts=out / "transcripts"
        )
        report = (
            f"# Scenario evaluation: {model}\n\nDate: {date.today().isoformat()} · "
            f"backend: `{args.backend}`\n\n" + report
        )
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").write_text(report)
        print(report)


if __name__ == "__main__":
    main()
