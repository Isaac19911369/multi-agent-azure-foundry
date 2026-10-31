import json

from incident_agents.agents import build_team
from incident_agents.llm import ChatResult, ScriptedChat, ToolCall
from incident_agents.orchestrator import Orchestrator, approve_all, deny_all
from incident_agents.plant import Plant


def call(name, **args):
    return ChatResult(None, [ToolCall(f"id-{name}", name, args)], 100, 10)


def text(t):
    return ChatResult(t, [], 100, 10)


def team(plant=None):
    plant = plant or Plant()
    specialists, reporter = build_team(plant)
    return plant, specialists, reporter


def test_handoff_tool_loop_and_report(spans):
    plant, specialists, reporter = team()
    model = ScriptedChat(
        [
            call("transfer_to_telemetry_analyst", task="look at P-101"),  # supervisor
            call("get_anomaly_events", asset_id="P-101"),  # telemetry
            text("vibration is up"),  # telemetry done
            call("finish"),  # supervisor
            text("## Report"),  # reporter
        ]
    )
    res = Orchestrator(model, specialists, reporter).run("alarm on P-101")
    assert res.status == "completed" and res.report == "## Report"
    assert res.handoffs == ["telemetry_analyst"]
    assert res.case_file == [{"agent": "telemetry_analyst", "findings": "vibration is up"}]
    # the specialist saw only its own tools; the reporter sees the case file
    tele_tools = {t["function"]["name"] for t in model.calls[1][1]}
    assert tele_tools == {"get_anomaly_events", "get_sensor_stats"}
    assert "vibration is up" in model.calls[4][0][1]["content"]
    names = [s.name for s in spans.get_finished_spans()]
    assert "invoke_agent supervisor" in names and "execute_tool get_anomaly_events" in names


def test_least_privilege_blocks_foreign_tools():
    plant, specialists, reporter = team()
    model = ScriptedChat(
        [
            call("transfer_to_telemetry_analyst", task="x"),
            call("create_work_order", asset_id="P-101", title="t", priority="P1"),  # not allowed
            text("done"),
            call("finish"),
            text("report"),
        ]
    )
    res = Orchestrator(model, specialists, reporter, approve_all).run("alarm")
    assert "not available" in res.tool_calls[0]["result"]["error"]
    assert plant.work_orders == []


def test_human_rejection_prevents_side_effect():
    plant, specialists, reporter = team()
    model = ScriptedChat(
        [
            call("transfer_to_maintenance_planner", task="plan"),
            call("create_work_order", asset_id="P-101", title="Bearing", priority="P2"),
            text("rejected"),
            call("finish"),
            text("report"),
        ]
    )
    res = Orchestrator(model, specialists, reporter, deny_all).run("alarm")
    assert res.approvals == [
        {
            "agent": "maintenance_planner",
            "tool": "create_work_order",
            "arguments": {"asset_id": "P-101", "title": "Bearing", "priority": "P2"},
            "approved": False,
        }
    ]
    assert res.tool_calls[0]["result"]["status"] == "rejected_by_human"
    assert plant.work_orders == []


def test_budget_and_handoff_limits():
    _, specialists, reporter = team()
    loop = [call("transfer_to_telemetry_analyst", task="again"), text("same")] * 10
    res = Orchestrator(ScriptedChat(loop + [text("r")]), specialists, reporter, max_handoffs=3).run(
        "alarm"
    )
    assert res.status == "max_handoffs" and len(res.handoffs) == 3

    big = ChatResult(
        None, [ToolCall("a", "transfer_to_telemetry_analyst", {"task": "t"})], 50_000, 20_000
    )
    res = Orchestrator(ScriptedChat([big]), specialists, reporter, token_budget=60_000).run("alarm")
    assert res.status == "budget_exceeded"
    assert json.dumps(res.to_dict())  # serialisable for the audit trail
