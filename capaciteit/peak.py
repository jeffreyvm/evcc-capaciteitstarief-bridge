"""Quarter-hour window accounting and month-peak tracking.

This is the piece Home Assistant used to provide via template sensors
(`remaining_energy_this_quarter_hour`, `effectieve_maandpiek`). Doing it here
removes the last reason for HA to be in the control path: evcc reports grid
power, and everything the capaciteitstarief needs is an integral over that.

Belgian capaciteitstarief in one paragraph: your monthly grid fee is driven by
the highest 15-minute *average* import of the month, with a billing floor of
2.5 kW. Short spikes are harmless; a sustained quarter is not. So the whole
system reasons about the running quarter, never about instantaneous power.

The functions here are pure. QuarterTracker holds the running integral and
MonthPeak holds persisted state; both are small and directly testable.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime

QUARTER_S = 900
CAP_TARIFF_FLOOR_KW = 2.5   # Belgian billing minimum


# --- pure math ---------------------------------------------------------------

def window_start_s(ts: float) -> float:
    """Unix timestamp of the start of the quarter `ts` falls in."""
    return ts - (ts % QUARTER_S)


def elapsed_s(ts: float) -> float:
    return ts % QUARTER_S


def remaining_s(ts: float) -> float:
    return QUARTER_S - elapsed_s(ts)


def projected_avg_w(consumed_wh: float, elapsed: float, current_w: float) -> float:
    """Quarter average if the current power holds for the rest of the window.

    This — not instantaneous power — is what can actually cost money.
    """
    remaining = QUARTER_S - elapsed
    if remaining <= 0:
        return consumed_wh * 3600 / QUARTER_S
    projected_wh = consumed_wh + current_w * remaining / 3600
    return projected_wh * 3600 / QUARTER_S


def allowed_power_w(target_w: float, consumed_wh: float, remaining: float) -> float:
    """Constant power that still lands the quarter average on `target_w`.

    Deliberately allowed to fall to zero: if the window's budget is already
    spent, no further import is free. Consumers must apply the breach floor
    (see logic.breach_floor_w) so a spent budget never triggers alarms below
    the 2.5 kW billing floor — that was the original false-positive bug.
    """
    if remaining <= 0:
        return 0.0
    budget_wh = target_w * QUARTER_S / 3600
    left_wh = budget_wh - consumed_wh
    if left_wh <= 0:
        return 0.0
    return left_wh * 3600 / remaining


def month_key(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m")


def effective_peak_kw(month_peak_kw: float) -> float:
    """The peak you are actually billed on — never below the 2.5 kW floor."""
    return max(month_peak_kw, CAP_TARIFF_FLOOR_KW)


# --- running state -----------------------------------------------------------

@dataclass
class QuarterTracker:
    """Trapezoidal integral of grid power over the running quarter.

    Only positive (import) power counts: exporting to the grid does not earn
    back capacity headroom under the Belgian tariff.
    """
    start_ts: float = 0.0
    consumed_wh: float = 0.0
    last_ts: float | None = None
    last_w: float = 0.0
    closed_avg_w: float | None = None   # average of the quarter that just ended

    def sample(self, ts: float, grid_w: float) -> None:
        self.closed_avg_w = None
        start = window_start_s(ts)
        if self.last_ts is None or start != self.start_ts:
            if self.last_ts is not None:
                # integrate the tail of the old window before rolling over
                tail = max(0.0, self.start_ts + QUARTER_S - self.last_ts)
                self.consumed_wh += max(0.0, self.last_w) * tail / 3600
                self.closed_avg_w = self.consumed_wh * 3600 / QUARTER_S
            self.start_ts = start
            self.consumed_wh = max(0.0, grid_w) * (ts - start) / 3600
        else:
            dt = ts - self.last_ts
            avg = (max(0.0, self.last_w) + max(0.0, grid_w)) / 2
            self.consumed_wh += avg * dt / 3600
        self.last_ts = ts
        self.last_w = grid_w

    def elapsed(self, ts: float) -> float:
        return elapsed_s(ts)

    def remaining(self, ts: float) -> float:
        return remaining_s(ts)

    def projected_w(self, ts: float, grid_w: float) -> float:
        return projected_avg_w(self.consumed_wh, elapsed_s(ts), grid_w)


@dataclass
class MonthPeak:
    """Highest closed quarter average of the current month, persisted to disk.

    evcc has no notion of the Belgian capacity tariff, so this is ours to keep.
    Written on change only; a corrupt or missing file starts a fresh month
    rather than crashing the loop.
    """
    path: str
    month: str = ""
    peak_w: float = 0.0
    manual_kw: float = 0.0   # optional override from config (TARGET_PEAK_KW)
    _loaded: bool = field(default=False, repr=False)

    def load(self) -> "MonthPeak":
        try:
            with open(self.path) as fh:
                data = json.load(fh)
            self.month = data.get("month", "")
            self.peak_w = float(data.get("peak_w", 0.0))
        except (OSError, ValueError, TypeError):
            self.month, self.peak_w = "", 0.0
        self._loaded = True
        return self

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = f"{self.path}.tmp"
            with open(tmp, "w") as fh:
                json.dump({"month": self.month, "peak_w": self.peak_w}, fh)
            os.replace(tmp, self.path)
        except OSError:
            pass  # never let bookkeeping take down the control loop

    def observe(self, ts: float, closed_avg_w: float) -> bool:
        """Record a completed quarter. Returns True if the peak changed."""
        key = month_key(ts)
        if key != self.month:
            self.month, self.peak_w = key, max(0.0, closed_avg_w)
            self.save()
            return True
        if closed_avg_w > self.peak_w:
            self.peak_w = closed_avg_w
            self.save()
            return True
        return False

    def effective_kw(self) -> float:
        """Manual override wins; otherwise the observed peak, floored at 2.5."""
        if self.manual_kw > 0:
            return effective_peak_kw(self.manual_kw)
        return effective_peak_kw(self.peak_w / 1000)
