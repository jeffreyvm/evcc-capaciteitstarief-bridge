"""Pure battery orchestration for peak shaving.

Same contract as logic.py: BatterySnapshot -> BatteryDecision, zero I/O.

The problem: batteries in self-consumption mode drain on evening baseload and
are empty when the real peak arrives. Self-consumption already *is* peak
shaving — as long as there is charge left. So this module manages state of
charge, not discharge:

  RESERVE   — hold a minimum SoC so baseload cannot drain the shaving buffer.
              Self-consumption above the reserve is untouched.
  PRECHARGE — ahead of a peak window, top up from the grid, capped by the same
              quarter budget the EV loop uses, so precharging can never itself
              set a new peak.
  RELEASE   — the projected quarter average approaches the billed month peak:
              drop to the hard floor and let the batteries shave. Hysteresis
              keeps it released until the danger clears, so charge is not
              dribbled away on ordinary load.

evcc's external battery mode (normal/hold/charge) is the actuator. Since
evcc has no per-SoC reserve, `hold` plus our own SoC comparison reproduces
one exactly: hold below the reserve, normal above it.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from capaciteit.logic import SAFETY_MARGIN

MIN_PRECHARGE_W = 300      # below this, charging is not worth the round trip
RELEASE_DEADBAND_W = 300   # leave RELEASE only this far below the threshold

# evcc external battery modes
MODE_NORMAL = "normal"
MODE_HOLD = "hold"
MODE_CHARGE = "charge"


@dataclass
class BatterySnapshot:
    """Everything decide_battery() needs, resolved by the caller."""
    now_s: int                    # seconds since local midnight
    grid_w: float                 # smoothed grid power (+ = import)
    quarter_projected_w: float    # projected 15-minute average
    effective_peak_kw: float      # month peak, already floored at 2.5
    allowed_power_w: float        # window budget, same as the EV loop
    battery_soc: float
    battery_max_charge_w: float
    currently_released: bool      # previous decision was RELEASE (hysteresis)
    # policy, injected from config so the dashboard can show what is in force
    reserve_soc: int = 40
    target_soc: int = 80
    hard_floor_soc: int = 10
    lead_s: int = 7200
    windows: tuple = ((7 * 3600, 9 * 3600), (17 * 3600, 21 * 3600))


@dataclass
class BatteryDecision:
    mode: str                     # reserve | precharge | release | idle
    evcc_mode: str                # normal | hold | charge
    min_soc: int
    grid_charge_w: int
    reason: str
    release_threshold_w: float
    in_peak_window: bool
    in_lead_window: bool

    def as_attributes(self) -> dict:
        return asdict(self)


def release_threshold_w(s: BatterySnapshot) -> float:
    """Shave once the projected quarter gets within the safety margin of the
    billed peak — the same 15% the EV loop uses."""
    return s.effective_peak_kw * 1000 * (1 - SAFETY_MARGIN)


def in_window(now_s: int, windows) -> bool:
    return any(start <= now_s < end for start, end in windows)


def in_lead(now_s: int, windows, lead_s: int) -> bool:
    return any(start - lead_s <= now_s < start for start, _ in windows)


def precharge_power_w(s: BatterySnapshot) -> int:
    """Grid charge power that fits inside the quarter budget.

    Reusing allowed_power_w means precharge and EV charging are arbitrated by
    one budget and can never stack into a new peak.
    """
    headroom = s.allowed_power_w * (1 - SAFETY_MARGIN) - s.grid_w
    return int(max(0, min(headroom, s.battery_max_charge_w)))


def decide_battery(s: BatterySnapshot) -> BatteryDecision:
    thresh = release_threshold_w(s)
    peak_win = in_window(s.now_s, s.windows)
    lead_win = in_lead(s.now_s, s.windows, s.lead_s)

    def make(mode, evcc_mode, min_soc, charge_w, reason):
        return BatteryDecision(
            mode=mode, evcc_mode=evcc_mode, min_soc=min_soc,
            grid_charge_w=charge_w, reason=reason, release_threshold_w=thresh,
            in_peak_window=peak_win, in_lead_window=lead_win)

    # 1. RELEASE — an imminent peak beats everything. Hysteresis on the exit.
    enter = s.quarter_projected_w >= thresh
    stay = s.currently_released and s.quarter_projected_w >= thresh - RELEASE_DEADBAND_W
    if enter or stay:
        if s.battery_soc <= s.hard_floor_soc:
            return make("release", MODE_HOLD, s.hard_floor_soc, 0,
                        f"Piek dreigt maar batterij op {s.battery_soc:.0f}% — "
                        f"harde bodem bereikt, niets meer te scheren")
        return make("release", MODE_NORMAL, s.hard_floor_soc, 0,
                    f"Kwartierprojectie {s.quarter_projected_w:.0f} W nadert de "
                    f"maandpiek ({thresh:.0f} W) — reserve vrijgegeven, batterij scheert")

    # 2. PRECHARGE — lead-up to a window, below target, budget available.
    if lead_win and s.battery_soc < s.target_soc:
        power = precharge_power_w(s)
        if power >= MIN_PRECHARGE_W:
            return make("precharge", MODE_CHARGE, s.reserve_soc, power,
                        f"Piekvenster nadert, SoC {s.battery_soc:.0f}% onder doel "
                        f"{s.target_soc}% — netladen op {power} W binnen kwartierbudget")
        return make("reserve", _hold_or_normal(s), s.reserve_soc, 0,
                    f"Piekvenster nadert maar geen laadbudget "
                    f"({power} W < {MIN_PRECHARGE_W} W) — reserve vasthouden")

    # 3. Inside a peak window below the threshold: keep the buffer for the
    #    quarter that actually threatens the peak, not the window's baseload.
    if peak_win:
        return make("reserve", _hold_or_normal(s), s.reserve_soc, 0,
                    f"In piekvenster, projectie {s.quarter_projected_w:.0f} W onder "
                    f"drempel — reserve {s.reserve_soc}% vasthouden voor de echte piek")

    # 4. Default: plain reserve, self-consumption free above it.
    return make("reserve", _hold_or_normal(s), s.reserve_soc, 0,
                f"Buiten piekvenster — zelfverbruik toegestaan boven "
                f"{s.reserve_soc}% reserve")


def _hold_or_normal(s: BatterySnapshot) -> str:
    """evcc has no SoC reserve; hold below it reproduces one exactly."""
    return MODE_HOLD if s.battery_soc <= s.reserve_soc else MODE_NORMAL
