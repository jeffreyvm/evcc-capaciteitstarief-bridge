"""Pure capaciteitstarief decision logic for the EV loadpoint.

Zero I/O. Snapshot -> Decision. The loop gathers the snapshot from evcc,
calls decide(), and applies the result. Nothing here knows evcc, HTTP, or
the web dashboard exist — which is what makes the arithmetic testable.

Two false positives from the original YAML implementation are pinned as
regression tests: a breach warning fired while the meter was *exporting*, and
a "current lowered" action that lowered 10 A to 10 A. Both came from the
window budget legitimately collapsing to near zero late in a quarter. The fix
is the breach floor: below the billed minimum, nothing that happens can cost
money, so nothing should raise an alarm.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

VOLTAGE = 230                 # default; snapshot carries the real value
SAFETY_MARGIN = 0.15          # regelvertraging headroom, used system-wide
CAP_TARIFF_FLOOR_KW = 2.5     # Belgian capaciteitstarief billing minimum
SOC_DISCHARGE_MIN = 25        # battery counts as headroom only above this SoC
BREACH_MIN_TIME_LEFT_S = 60   # don't warn in the dying seconds of a quarter
STEP_DOWN_DEADBAND_A = 3      # bigger drops apply at once, smaller need hysteresis


@dataclass
class Snapshot:
    """Everything decide() needs, already resolved to floats by the caller."""
    # smoothed inputs (60 s moving averages where available)
    grid_w: float
    charger_w: float
    battery_net_w: float          # + = discharging to house, - = charging
    # instantaneous grid power; breach detection stays on raw, by design
    grid_instant_w: float
    # window-aware budget from peak.allowed_power_w
    allowed_power_w: float
    remaining_time_s: float
    # battery discharge capability and SoC gating
    battery_discharge_w: float
    battery_soc: float
    effective_peak_kw: float      # already floored at 2.5
    # charger state
    charge_current_a: float       # >0 means actually charging
    max_current_setpoint_a: int
    recommended_peak_60s_a: int   # rolling 60 s max of our own recommendation
    # electrical context
    phases: int = 1
    voltage: int = VOLTAGE
    min_a: int = 6
    max_a: int = 32


@dataclass
class Decision:
    new_setpoint_a: Optional[int]   # None = leave the setpoint alone
    breach: bool
    recommended_a: int
    status: str                     # normaal | verhogen | verlagen | breach_floored | idle
    reason: str                     # human-readable why, shown on the dashboard
    allowed_w: float
    net_surplus_w: float
    discharge_w: float
    budget_w: float
    breach_floor_w: float

    def as_attributes(self) -> dict:
        return asdict(self)


def breach_floor_w(s: Snapshot) -> float:
    """Below this, a breach cannot cost anything, so it is not a breach.

    The tariff bills max(real_peak, 2.5 kW). Warning about power under the
    billed minimum is noise — this floor is the whole fix for the two
    original false positives.
    """
    return s.effective_peak_kw * 1000 * (1 - SAFETY_MARGIN)


def is_breach(s: Snapshot) -> bool:
    if s.remaining_time_s < BREACH_MIN_TIME_LEFT_S:
        return False
    if s.grid_instant_w < breach_floor_w(s):
        return False
    return s.grid_instant_w > s.allowed_power_w


def recommended_current(s: Snapshot) -> tuple[int, dict]:
    """Highest charge current that keeps the quarter under the month peak.

    Budget = window grid headroom + power the charger is already drawing
    (that share is reallocatable) + battery discharge still available,
    minus the safety margin.
    """
    discharge = s.battery_discharge_w if s.battery_soc >= SOC_DISCHARGE_MIN else 0.0
    net_surplus = max(0.0, s.charger_w)
    budget = (s.allowed_power_w + net_surplus + discharge) * (1 - SAFETY_MARGIN)
    amps = int(budget // (s.voltage * max(1, s.phases)))
    amps = max(s.min_a, min(s.max_a, amps))
    return amps, {
        "allowed_w": s.allowed_power_w,
        "net_surplus_w": net_surplus,
        "discharge_w": discharge,
        "budget_w": budget,
    }


def decide(s: Snapshot) -> Decision:
    amps, bd = recommended_current(s)
    breach = is_breach(s)
    floor = breach_floor_w(s)

    def make(setpoint, status, reason):
        return Decision(
            new_setpoint_a=setpoint, breach=breach, recommended_a=amps,
            status=status, reason=reason, breach_floor_w=floor, **bd)

    if s.charge_current_a <= 0:
        return make(None, "idle", "Geen laadsessie actief — geen ingreep nodig")

    current = s.max_current_setpoint_a

    if amps > current:
        return make(amps, "verhogen",
                    f"Budget laat {amps} A toe (nu {current} A) — verhogen")

    if amps <= current - STEP_DOWN_DEADBAND_A:
        return make(amps, "verlagen",
                    f"Budget nog {amps} A, {current - amps} A te veel — direct verlagen")

    if amps < current:
        # small drop: only act once the 60 s rolling maximum agrees
        if s.recommended_peak_60s_a < current:
            return make(amps, "verlagen",
                        f"Aanbeveling 60 s stabiel op {amps} A — verlagen van {current} A")
        return make(None, "normaal",
                    f"Kleine daling naar {amps} A nog niet stabiel — {current} A vasthouden")

    return make(None, "normaal", f"Setpoint {current} A past binnen het budget")
