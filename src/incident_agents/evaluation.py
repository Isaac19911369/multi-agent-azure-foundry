"""Scenario-based evaluation of the agent team.

Each scenario fixes the plant state, the approval decision and the expectations:
which specialists must (not) be involved, which tools must (not) be called, what
the report must mention, and whether a work order may exist. The same suite runs
against the offline demo policy (a regression test of the orchestration) and
against a real Azure AI Foundry deployment (a test of the model's behaviour).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import yaml

from .agents import build_team
from .orchestrator import Orchestrator, approve_all, deny_all
from .plant import Plant


def check(scenario: dict, res, plant: Plant) -> dict[str, bool]:
    exp = scenario["expect"]
    called = {c["tool"] for c in res.tool_calls}
    report = res.report.lower()
    checks = {
        "completed": res.status == "completed",
        "handoffs": all(h in res.handoffs for h in exp.get("handoffs", []))
        and not any(h in res.handoffs for h in exp.get("no_handoffs", [])),
        "tools": all(t in called for t in exp.get("tools", []))
        and not any(t in called for t in exp.get("no_tools", [])),
        "report": all(k.lower() in report for k in exp.get("report_contains", [])),
        "work_order": (len(plant.work_orders) > 0) == exp.get("work_order_created", False),
        "within_budget": res.input_tokens + res.output_tokens <= exp.get("max_tokens", 60_000),
    }
    if "priority" in exp:
        checks["priority"] = any(
            c["arguments"].get("priority") == exp["priority"]
            for c in res.tool_calls
            if c["tool"] == "create_work_order"
        )
    return checks


def run_suite(directory: Path, model_factory: Callable, transcripts: Path | None = None) -> str:
    rows, passed = [], 0
    files = sorted(directory.glob("*.yaml"))
    for f in files:
        sc = yaml.safe_load(f.read_text())
        plant = Plant(assets={sc["asset"]: sc["mode"]})
        if "inventory" in sc:
            plant.inventory.update(sc["inventory"])
        specialists, reporter = build_team(plant)
        orch = Orchestrator(
            model_factory(),
            specialists,
            reporter,
            approve_all if sc.get("approve", True) else deny_all,
        )
        try:
            res = orch.run(sc["incident"])
        except Exception as exc:  # e.g. API errors: record and continue with the suite
            rows.append(f"| {sc['name']} | ERROR: {type(exc).__name__}: {str(exc)[:80]} | | | |")
            continue
        if transcripts is not None:
            transcripts.mkdir(parents=True, exist_ok=True)
            (transcripts / f"{f.stem}.json").write_text(
                json.dumps(res.to_dict(), indent=2, default=str)
            )
        checks = check(sc, res, plant)
        ok = all(checks.values())
        passed += ok
        failed = [k for k, v in checks.items() if not v]
        rows.append(
            f"| {sc['name']} | {'pass' if ok else 'FAIL: ' + ', '.join(failed)} | "
            f"{' → '.join(res.handoffs)} | {len(res.tool_calls)} | "
            f"{res.input_tokens + res.output_tokens} |"
        )
    head = ["| Scenario | Result | Handoffs | Tool calls | Tokens |", "|---|---|---|---|---|"]
    return "\n".join(head + rows) + f"\n\n**{passed}/{len(files)} scenarios passed**\n"
