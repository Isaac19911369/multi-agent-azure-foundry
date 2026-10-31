"""Simulated plant systems the agents work with.

Stand-ins for a historian / time-series DB (telemetry), the anomaly-detection
service, the CMMS (maintenance history, work orders), the spare-parts inventory
and the equipment manuals. Everything is deterministic so scenarios are
reproducible. Real deployments replace each method with an API call.
"""

from __future__ import annotations

import itertools
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MANUAL_DIR = Path(__file__).parent / "manual"

# failure mode -> how sensors drift over the last 72 hours
_PROFILES = {
    "bearing_wear": {
        "vibration_rms": (2.1, 5.6),
        "bearing_temp": (60.0, 74.0),
        "flow": (120.0, 118.0),
        "discharge_pressure": (6.0, 5.9),
    },
    "imbalance": {
        "vibration_rms": (2.0, 5.1),
        "bearing_temp": (61.0, 61.5),
        "flow": (120.0, 113.0),
        "discharge_pressure": (6.0, 5.8),
    },
    "cavitation": {
        "vibration_rms": (2.0, 3.6),
        "bearing_temp": (60.0, 61.0),
        "flow": (120.0, 96.0),
        "discharge_pressure": (6.0, 4.4),
    },
    "healthy": {
        "vibration_rms": (2.0, 2.1),
        "bearing_temp": (60.0, 60.5),
        "flow": (120.0, 120.0),
        "discharge_pressure": (6.0, 6.0),
    },
}


@dataclass
class Plant:
    """One site. ``assets`` maps asset id -> failure mode currently developing."""

    assets: dict[str, str] = field(default_factory=lambda: {"P-101": "bearing_wear"})
    inventory: dict[str, int] = field(
        default_factory=lambda: {"BRG-6310": 2, "SEAL-220": 0, "STR-80": 5}
    )
    history: dict[str, list[dict]] = field(
        default_factory=lambda: {
            "P-101": [
                {"date": "2026-03-14", "work": "Re-greased bearings", "technician": "M. Huber"},
                {
                    "date": "2025-09-02",
                    "work": "Replaced mechanical seal SEAL-220",
                    "technician": "A. Novak",
                },
            ]
        }
    )
    work_orders: list[dict] = field(default_factory=list)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(5001))

    # ------------------------------------------------------------------ telemetry
    def series(self, asset_id: str, sensor: str, hours: int = 72) -> np.ndarray:
        mode = self.assets.get(asset_id)
        if mode is None:
            raise KeyError(f"unknown asset {asset_id}")
        start, end = _PROFILES[mode][sensor]
        t = np.linspace(0, 1, hours)
        rng = np.random.default_rng(zlib.crc32(f"{asset_id}/{sensor}".encode()))
        curve = start + (end - start) * t**2  # degradation accelerates
        return curve + rng.normal(0, 0.02 * max(abs(start), 1e-3), hours)

    def sensor_stats(self, asset_id: str, sensor: str, hours: int = 72) -> dict:
        s = self.series(asset_id, sensor, hours)
        first, last = s[: hours // 6].mean(), s[-hours // 6 :].mean()
        slope_per_day = float(np.polyfit(np.arange(hours) / 24, s, 1)[0])
        return {
            "asset_id": asset_id,
            "sensor": sensor,
            "hours": hours,
            "baseline": round(float(first), 2),
            "current": round(float(last), 2),
            "change": round(float(last - first), 2),
            "max": round(float(s.max()), 2),
            "trend_per_day": round(slope_per_day, 3),
        }

    def anomaly_events(self, asset_id: str, hours: int = 72) -> list[dict]:
        events = []
        for sensor in _PROFILES[self.assets[asset_id]]:
            st = self.sensor_stats(asset_id, sensor, hours)
            rel = abs(st["change"]) / max(abs(st["baseline"]), 1e-6)
            if rel > 0.1:
                events.append(
                    {
                        "sensor": sensor,
                        "score": round(min(rel * 3, 1.0), 2),
                        "direction": "up" if st["change"] > 0 else "down",
                    }
                )
        return sorted(events, key=lambda e: -e["score"])

    # ------------------------------------------------------------------ knowledge
    def search_manual(self, query: str, k: int = 2) -> list[dict]:
        q = set(re.findall(r"[a-z]+", query.lower()))
        scored = []
        for f in sorted(MANUAL_DIR.glob("*.md")):
            text = f.read_text()
            words = re.findall(r"[a-z]+", text.lower())
            score = sum(words.count(t) for t in q if len(t) > 3)
            scored.append((score, f.stem, text))
        scored.sort(key=lambda x: -x[0])
        return [{"doc": name, "text": text} for score, name, text in scored[:k] if score > 0]

    def maintenance_history(self, asset_id: str) -> list[dict]:
        return self.history.get(asset_id, [])

    # ------------------------------------------------------------------ CMMS
    def spare_parts(self, part_no: str) -> dict:
        qty = self.inventory.get(part_no.upper())
        if qty is None:
            return {"part_no": part_no, "error": "unknown part"}
        return {"part_no": part_no.upper(), "in_stock": qty, "lead_time_days": 0 if qty else 6}

    def create_work_order(self, asset_id: str, title: str, priority: str, parts: str = "") -> dict:
        wo = {
            "id": f"WO-{next(self._ids)}",
            "asset_id": asset_id,
            "title": title,
            "priority": priority,
            "parts": [p for p in parts.split(",") if p.strip()],
            "status": "Planned",
        }
        self.work_orders.append(wo)
        return wo
