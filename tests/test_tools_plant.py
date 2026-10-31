import json

from incident_agents.plant import Plant
from incident_agents.tools import Tool


def test_schema_from_type_hints():
    def f(asset_id: str, hours: int = 72) -> dict:
        """Do something.

        asset_id: the asset
        """
        return {"asset_id": asset_id, "hours": hours}

    t = Tool.from_function(f)
    assert t.parameters["required"] == ["asset_id"]
    assert t.parameters["properties"]["hours"]["type"] == "integer"
    assert t.parameters["properties"]["asset_id"]["description"] == "the asset"
    assert json.loads(t.invoke({"asset_id": "P-1"}))["hours"] == 72
    assert "error" in json.loads(t.invoke({"hours": 1}))
    assert "error" in json.loads(t.invoke({"asset_id": "x", "evil": 1}))


def test_tool_exceptions_become_errors():
    def boom(x: str) -> dict:
        """Fails."""
        raise RuntimeError("db down")

    assert "db down" in json.loads(Tool.from_function(boom).invoke({"x": "1"}))["error"]


def test_plant_is_deterministic_and_reflects_failure_mode():
    a, b = Plant(), Plant()
    assert a.sensor_stats("P-101", "vibration_rms") == b.sensor_stats("P-101", "vibration_rms")
    events = {e["sensor"] for e in a.anomaly_events("P-101")}
    assert {"vibration_rms", "bearing_temp"} <= events
    assert Plant(assets={"X-001": "healthy"}).anomaly_events("X-001") == []
    assert Plant().search_manual("cavitation flow drop")[0]["doc"] == "pump-p100-cavitation"
