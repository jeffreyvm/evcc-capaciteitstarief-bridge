"""EV charging logic.

The two tests named after screenshots reproduce false positives from the
original YAML implementation. They were debugged from phone screenshots once;
now they run in a second.
"""
from capaciteit.logic import (
    Snapshot, decide, recommended_current, is_breach, breach_floor_w,
    STEP_DOWN_DEADBAND_A,
)


def make_snapshot(**overrides) -> Snapshot:
    """Benign baseline: charging at 16 A, plenty of budget, no breach."""
    base = dict(
        grid_w=0.0,
        charger_w=3680.0,
        battery_net_w=0.0,
        grid_instant_w=0.0,
        allowed_power_w=5000.0,
        remaining_time_s=600,
        battery_discharge_w=2300.0,
        battery_soc=80.0,
        effective_peak_kw=5.0,
        charge_current_a=16.0,
        max_current_setpoint_a=16,
        recommended_peak_60s_a=16,
        phases=1,
        voltage=230,
        min_a=6,
        max_a=32,
    )
    base.update(overrides)
    return Snapshot(**base)


# --- recommended current ---

def test_recommended_clamps_to_max():
    assert recommended_current(make_snapshot(allowed_power_w=50000))[0] == 32


def test_recommended_clamps_to_min():
    s = make_snapshot(allowed_power_w=100, charger_w=0, battery_soc=0)
    assert recommended_current(s)[0] == 6


def test_soc_gate_excludes_low_battery():
    _, high = recommended_current(make_snapshot(battery_soc=80))
    _, low = recommended_current(make_snapshot(battery_soc=10))
    assert high["discharge_w"] == 2300.0
    assert low["discharge_w"] == 0.0


def test_safety_margin_applied():
    s = make_snapshot(allowed_power_w=4600, charger_w=0, battery_soc=0)
    assert recommended_current(s)[1]["budget_w"] == 4600 * 0.85


def test_three_phase_halves_nothing_but_divides_by_three():
    single = recommended_current(make_snapshot(allowed_power_w=6900, charger_w=0,
                                               battery_soc=0, phases=1))[0]
    three = recommended_current(make_snapshot(allowed_power_w=6900, charger_w=0,
                                              battery_soc=0, phases=3))[0]
    assert single == 25 and three == 8


# --- breach detection ---

def test_breach_when_instant_exceeds_budget():
    assert is_breach(make_snapshot(grid_instant_w=6000, allowed_power_w=4000))


def test_no_breach_in_final_seconds():
    s = make_snapshot(grid_instant_w=6000, allowed_power_w=4000, remaining_time_s=30)
    assert not is_breach(s)


def test_breach_floor_tracks_effective_peak():
    assert breach_floor_w(make_snapshot(effective_peak_kw=2.5)) == 2125.0


# --- the original false positives ---

def test_screenshot_1_no_breach_while_exporting():
    """'Maandpiek wordt overschreden!' fired at -0.04 kW: the budget had
    collapsed after an early spike, so any blip cleared it."""
    s = make_snapshot(grid_instant_w=-40.0, allowed_power_w=0.0,
                      effective_peak_kw=2.5)
    assert not is_breach(s)


def test_screenshot_2_no_pointless_setpoint_write():
    """'Laadstroom verlaagd' fired as a 10 A -> 10 A no-op."""
    s = make_snapshot(allowed_power_w=2300, charger_w=0, battery_soc=0,
                      max_current_setpoint_a=10, recommended_peak_60s_a=10,
                      charge_current_a=10)
    d = decide(s)
    assert d.new_setpoint_a is None
    assert d.status == "normaal"


def test_sub_floor_load_never_breaches():
    """Nothing under the 2.5 kW billing minimum can cost money."""
    s = make_snapshot(grid_instant_w=1800, allowed_power_w=0.0,
                      effective_peak_kw=2.5)
    assert not is_breach(s)


# --- setpoint decisions ---

def test_increase_applies_immediately():
    d = decide(make_snapshot(allowed_power_w=50000, max_current_setpoint_a=10))
    assert d.new_setpoint_a == 32 and d.status == "verhogen"


def test_large_drop_applies_immediately():
    d = decide(make_snapshot(allowed_power_w=2000, charger_w=0, battery_soc=0,
                             max_current_setpoint_a=32, recommended_peak_60s_a=32))
    assert d.new_setpoint_a is not None
    assert d.new_setpoint_a <= 32 - STEP_DOWN_DEADBAND_A
    assert d.status == "verlagen"


def test_small_drop_waits_for_stable_60s():
    d = decide(make_snapshot(allowed_power_w=3450, charger_w=0, battery_soc=0,
                             max_current_setpoint_a=14, recommended_peak_60s_a=14))
    assert d.new_setpoint_a is None
    assert d.status == "normaal"


def test_small_drop_applies_once_stable():
    d = decide(make_snapshot(allowed_power_w=3450, charger_w=0, battery_soc=0,
                             max_current_setpoint_a=14, recommended_peak_60s_a=12))
    assert d.new_setpoint_a == 12 and d.status == "verlagen"


def test_idle_never_touches_the_setpoint():
    d = decide(make_snapshot(charge_current_a=0.0, allowed_power_w=0.0))
    assert d.new_setpoint_a is None and d.status == "idle"


def test_decision_serializes_for_the_dashboard():
    attrs = decide(make_snapshot()).as_attributes()
    for key in ("reason", "status", "recommended_a", "budget_w", "breach_floor_w"):
        assert key in attrs
