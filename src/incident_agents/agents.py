"""The agent team: one supervisor and four specialists with least-privilege tools."""

from __future__ import annotations

from dataclasses import dataclass, field

from .plant import Plant
from .tools import Tool


@dataclass
class Agent:
    name: str
    description: str
    instructions: str
    tools: list[Tool] = field(default_factory=list)
    max_steps: int = 6

    def system_prompt(self) -> str:
        return f"ROLE: {self.name}\n{self.instructions}"


SUPERVISOR_INSTRUCTIONS = """You coordinate the triage of an industrial equipment incident.
Delegate work to specialists with the transfer_to_* tools, giving each a precise task.
Typical order: telemetry evidence first, then diagnosis from manuals and history, then
maintenance planning. Skip specialists that are not needed (e.g. no planning when the
asset is healthy). When the case file is sufficient, call finish. Never invent facts."""


def build_team(plant: Plant) -> tuple[list[Agent], Agent]:
    """Specialists and the reporter, each with only the tools it needs."""

    def get_anomaly_events(asset_id: str, hours: int = 72) -> list[dict]:
        """List sensors of an asset with anomalous behaviour, highest score first.

        asset_id: Asset tag such as P-101
        hours: Look-back window in hours
        """
        return plant.anomaly_events(asset_id, hours)

    def get_sensor_stats(asset_id: str, sensor: str, hours: int = 72) -> dict:
        """Baseline, current value, change and daily trend of one sensor.

        asset_id: Asset tag such as P-101
        sensor: vibration_rms | bearing_temp | flow | discharge_pressure
        hours: Look-back window in hours
        """
        return plant.sensor_stats(asset_id, sensor, hours)

    def search_manual(query: str) -> list[dict]:
        """Search the equipment manuals for failure modes, limits and repair procedures.

        query: Symptoms or question in natural language
        """
        return plant.search_manual(query)

    def get_maintenance_history(asset_id: str) -> list[dict]:
        """Past maintenance work on an asset from the CMMS.

        asset_id: Asset tag such as P-101
        """
        return plant.maintenance_history(asset_id)

    def check_spare_parts(part_no: str) -> dict:
        """Stock level and lead time of a spare part.

        part_no: Part number such as BRG-6310
        """
        return plant.spare_parts(part_no)

    def create_work_order(asset_id: str, title: str, priority: str, parts: str = "") -> dict:
        """Create a maintenance work order in the CMMS (requires human approval).

        asset_id: Asset tag
        title: Short description of the work
        priority: P1 (within 24 h) | P2 (within 72 h) | P3 (next planned stop)
        parts: Comma-separated part numbers to reserve
        """
        return plant.create_work_order(asset_id, title, priority, parts)

    telemetry = Agent(
        "telemetry_analyst",
        "Quantifies what the sensors show: which signals deviate, by how much, how fast.",
        "You analyse sensor telemetry. Call get_anomaly_events, then get_sensor_stats for "
        "the relevant sensors. Report numbers with units and trends. Do not diagnose.",
        [Tool.from_function(get_anomaly_events), Tool.from_function(get_sensor_stats)],
    )
    expert = Agent(
        "maintenance_expert",
        "Diagnoses the probable root cause from manuals and maintenance history.",
        "You are a reliability engineer. Use search_manual and get_maintenance_history to "
        "determine the most probable cause, the severity zone, the required parts and the "
        "downtime. Cite the manual section you rely on.",
        [Tool.from_function(search_manual), Tool.from_function(get_maintenance_history)],
    )
    planner = Agent(
        "maintenance_planner",
        "Checks spare parts and creates the work order (human approval required).",
        "You plan maintenance. Check spare parts, then create a work order with a priority "
        "that respects the safety limits. If a part is out of stock, say so and propose "
        "an alternative. Work orders are only created after human approval.",
        [
            Tool.from_function(check_spare_parts),
            Tool.from_function(create_work_order, requires_approval=True),
        ],
    )
    reporter = Agent(
        "reporter",
        "Writes the final incident report.",
        "Write a concise incident report in Markdown with the sections Summary, Evidence, "
        "Diagnosis, Actions, Open points. Only use facts from the case file.",
    )
    return [telemetry, expert, planner], reporter
