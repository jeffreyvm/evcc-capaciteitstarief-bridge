"""In-memory state for the dashboard.

Deliberately not a database. evcc owns session history; this holds one hour of
samples so the dashboard can draw a timeline, plus the rolling windows the
control logic needs. Losing it on restart costs a graph, not a decision.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Optional

HISTORY_S = 3600


@dataclass
class Rolling:
    """Time-bounded window over (timestamp, value) pairs."""
    seconds: float
    points: Deque[tuple[float, float]] = field(default_factory=deque)

    def add(self, ts: float, value: float) -> None:
        self.points.append((ts, value))
        cutoff = ts - self.seconds
        while self.points and self.points[0][0] < cutoff:
            self.points.popleft()

    def mean(self, default: float = 0.0) -> float:
        if not self.points:
            return default
        return sum(v for _, v in self.points) / len(self.points)

    def max(self, default: float = 0.0) -> float:
        if not self.points:
            return default
        return max(v for _, v in self.points)


@dataclass
class Store:
    history: Deque[dict] = field(default_factory=deque)
    latest: Optional[dict] = None
    errors: Deque[dict] = field(default_factory=lambda: deque(maxlen=25))
    started_at: float = field(default_factory=time.time)
    # rolling windows shared with the control logic
    grid_60s: Rolling = field(default_factory=lambda: Rolling(60))
    charger_60s: Rolling = field(default_factory=lambda: Rolling(60))
    battery_60s: Rolling = field(default_factory=lambda: Rolling(60))
    recommended_60s: Rolling = field(default_factory=lambda: Rolling(60))

    def record(self, snapshot: dict) -> None:
        self.latest = snapshot
        self.history.append(snapshot)
        cutoff = snapshot["ts"] - HISTORY_S
        while self.history and self.history[0]["ts"] < cutoff:
            self.history.popleft()

    def note_error(self, message: str) -> None:
        self.errors.append({"ts": time.time(), "message": message})

    def timeline(self) -> list[dict]:
        """Compact series for the dashboard chart."""
        return [
            {
                "ts": h["ts"],
                "grid_w": h["grid_w"],
                "projected_w": h["projected_w"],
                "allowed_w": h["allowed_w"],
                "soc": h["battery_soc"],
            }
            for h in self.history
        ]

    def payload(self, config_public: dict) -> dict[str, Any]:
        return {
            "now": time.time(),
            "uptime_s": time.time() - self.started_at,
            "config": config_public,
            "latest": self.latest,
            "timeline": self.timeline(),
            "errors": list(self.errors),
        }
