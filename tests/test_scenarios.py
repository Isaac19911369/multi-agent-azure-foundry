from pathlib import Path

from incident_agents.demo_policy import DemoPolicy
from incident_agents.evaluation import run_suite


def test_scenario_suite_passes_with_demo_policy():
    report = run_suite(Path("scenarios"), DemoPolicy)
    assert "FAIL" not in report
    assert "5/5 scenarios passed" in report
