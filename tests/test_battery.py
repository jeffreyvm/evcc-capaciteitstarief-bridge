"""Battery orchestration.

The motivating scenario is pinned at the bottom: batteries drained by evening
baseload, empty when the peak arrives.
"""
from capaciteit.battery import (
    BatterySnapshot, decide_battery, precharge_power_w, release_threshold_w,
    RELEASE_DEADBAND_W, MODE_NORMAL, MODE_HOLD, MODE_CHARGE,
)


def make_snapshot(**overrides) -> BatterySnapshot:
    """Benign baseline: 14:00, low load, healthy SoC, 5 kW month peak."""
    base = dict(
        now_s=14 * 3600,
        grid_w=400.0,
        quarter_projected_w=400.0,
        effective_peak_kw=5.0,
        allowed_power_w=5000.0,
        battery_soc=70.0,
        battery_max_charge_w=2400.0,
        currently_released=False,
    )
    base.update(overrides)
    return BatterySnapshot(**base)


# --- reserve ---

def test_default_is_reserve_with_self_consumption_allowed():
    d = decide_battery(make_snapshot())
    assert d.mode == "reserve"
    assert d.evcc_mode == MODE_NORMAL      # above reserve: evcc keeps discharging
    assert d.grid_charge_w == 0


def test_reserve_holds_battery_once_at_the_reserve_level():
    d = decide_battery(make_snapshot(battery_soc=35))
    assert d.mode == "reserve"
    assert d.evcc_mode == MODE_HOLD        # this is what stops the drain


def test_reserve_holds_inside_peak_window_below_threshold():
    d = decide_battery(make_snapshot(now_s=18 * 3600, quarter_projected_w=2000))
    assert d.mode == "reserve" and d.in_peak_window


# --- release ---

def test_release_when_projection_nears_month_peak():
    d = decide_battery(make_snapshot(now_s=18 * 3600, quarter_projected_w=4300))
    assert d.mode == "release"
    assert d.evcc_mode == MODE_NORMAL
    assert d.min_soc == 10


def test_release_fires_outside_windows_too():
    assert decide_battery(make_snapshot(quarter_projected_w=4400)).mode == "release"


def test_release_hysteresis_holds_within_deadband():
    thresh = release_threshold_w(make_snapshot())
    d = decide_battery(make_snapshot(
        quarter_projected_w=thresh - RELEASE_DEADBAND_W / 2, currently_released=True))
    assert d.mode == "release"


def test_release_exits_below_deadband():
    thresh = release_threshold_w(make_snapshot())
    d = decide_battery(make_snapshot(
        quarter_projected_w=thresh - RELEASE_DEADBAND_W * 2, currently_released=True))
    assert d.mode == "reserve"


def test_release_at_hard_floor_stops_discharging():
    d = decide_battery(make_snapshot(quarter_projected_w=4500, battery_soc=8))
    assert d.mode == "release" and d.evcc_mode == MODE_HOLD


def test_threshold_uses_billing_floor():
    assert release_threshold_w(make_snapshot(effective_peak_kw=2.5)) == 2125.0


# --- precharge ---

def test_precharge_in_lead_window_below_target():
    d = decide_battery(make_snapshot(now_s=15 * 3600 + 1800, battery_soc=40))
    assert d.mode == "precharge"
    assert d.evcc_mode == MODE_CHARGE
    assert d.grid_charge_w > 0


def test_precharge_capped_by_quarter_budget():
    # 3000 * 0.85 - 2000 = 550
    assert precharge_power_w(make_snapshot(allowed_power_w=3000, grid_w=2000)) == 550


def test_precharge_capped_by_battery_capability():
    s = make_snapshot(allowed_power_w=20000, grid_w=0, battery_max_charge_w=2400)
    assert precharge_power_w(s) == 2400


def test_no_precharge_without_budget():
    d = decide_battery(make_snapshot(now_s=16 * 3600, battery_soc=40,
                                     allowed_power_w=3000, grid_w=2900))
    assert d.mode == "reserve" and d.grid_charge_w == 0


def test_no_precharge_when_already_at_target():
    d = decide_battery(make_snapshot(now_s=16 * 3600, battery_soc=85))
    assert d.mode == "reserve"


def test_morning_window_has_a_lead_period_too():
    d = decide_battery(make_snapshot(now_s=6 * 3600, battery_soc=30))
    assert d.mode == "precharge"


# --- the motivating scenario ---

def test_empty_at_peak_scenario_is_prevented():
    """Evening baseload can only drain to the reserve; when the projection
    crosses the threshold, the buffer is released for the peak quarter."""
    late_evening = decide_battery(make_snapshot(now_s=22 * 3600,
                                                quarter_projected_w=800,
                                                battery_soc=40))
    assert late_evening.evcc_mode == MODE_HOLD     # drain stops at the reserve

    the_peak = decide_battery(make_snapshot(now_s=18 * 3600,
                                            quarter_projected_w=4500,
                                            battery_soc=40))
    assert the_peak.mode == "release"
    assert the_peak.evcc_mode == MODE_NORMAL       # full buffer now available
    assert the_peak.min_soc == 10


def test_decision_serializes_for_the_dashboard():
    attrs = decide_battery(make_snapshot()).as_attributes()
    for key in ("mode", "evcc_mode", "reason", "release_threshold_w", "min_soc"):
        assert key in attrs
