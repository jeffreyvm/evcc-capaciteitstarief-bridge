"""Quarter-hour accounting: the arithmetic the whole system rests on."""
import os
import tempfile

from capaciteit.peak import (
    QUARTER_S, QuarterTracker, MonthPeak, allowed_power_w, projected_avg_w,
    effective_peak_kw, elapsed_s, remaining_s, window_start_s,
)

# A timestamp exactly on a quarter boundary keeps the arithmetic readable.
T0 = 1_700_000_100 - (1_700_000_100 % QUARTER_S)


def test_window_boundaries():
    assert window_start_s(T0 + 300) == T0
    assert elapsed_s(T0 + 300) == 300
    assert remaining_s(T0 + 300) == 600


def test_projection_holds_current_power():
    # 300 s in, 250 Wh used (=3 kW so far), still pulling 3 kW -> 3 kW average
    assert round(projected_avg_w(250.0, 300, 3000.0)) == 3000


def test_projection_falls_when_load_drops():
    # same history, but load dropped to zero -> only what is already used counts
    assert round(projected_avg_w(250.0, 300, 0.0)) == 1000


def test_allowed_power_is_full_budget_at_window_start():
    # 5 kW target, nothing used, full 900 s left
    assert round(allowed_power_w(5000, 0.0, 900)) == 5000


def test_allowed_power_shrinks_after_early_spike():
    # 5 kW target = 1250 Wh budget; 1000 Wh burned with 450 s left
    assert round(allowed_power_w(5000, 1000.0, 450)) == 2000


def test_allowed_power_floors_at_zero_when_budget_spent():
    # this collapse is legitimate — the breach floor in logic.py handles it
    assert allowed_power_w(5000, 2000.0, 300) == 0.0


def test_effective_peak_respects_belgian_minimum():
    assert effective_peak_kw(1.2) == 2.5
    assert effective_peak_kw(4.8) == 4.8


# --- tracker ---

def test_tracker_integrates_constant_load():
    t = QuarterTracker()
    t.sample(T0, 3600.0)
    t.sample(T0 + 3600 / 3600 * 3600, 3600.0)  # +1 h would roll; use 60 s instead
    t2 = QuarterTracker()
    t2.sample(T0, 3600.0)
    t2.sample(T0 + 60, 3600.0)
    assert round(t2.consumed_wh, 3) == 60.0   # 3.6 kW for 60 s = 60 Wh


def test_tracker_ignores_export():
    t = QuarterTracker()
    t.sample(T0, -2000.0)
    t.sample(T0 + 60, -2000.0)
    assert t.consumed_wh == 0.0


def test_tracker_rolls_over_and_closes_the_quarter():
    t = QuarterTracker()
    t.sample(T0, 4000.0)
    t.sample(T0 + 450, 4000.0)
    assert t.closed_avg_w is None
    t.sample(T0 + QUARTER_S + 30, 4000.0)      # first sample of the next window
    assert t.closed_avg_w is not None
    assert round(t.closed_avg_w) == 4000       # constant 4 kW across the quarter
    assert t.start_ts == T0 + QUARTER_S


def test_tracker_projection_uses_running_integral():
    t = QuarterTracker()
    t.sample(T0, 6000.0)
    t.sample(T0 + 300, 6000.0)
    assert round(t.projected_w(T0 + 300, 6000.0)) == 6000
    assert round(t.projected_w(T0 + 300, 0.0)) == 2000


# --- month peak ---

def _peak() -> MonthPeak:
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    return MonthPeak(path).load()


def test_month_peak_records_and_persists():
    p = _peak()
    assert p.observe(T0, 3200.0) is True
    assert p.observe(T0 + QUARTER_S, 2800.0) is False   # lower quarter, no change
    assert p.observe(T0 + 2 * QUARTER_S, 4100.0) is True
    reloaded = MonthPeak(p.path).load()
    assert round(reloaded.peak_w) == 4100


def test_month_peak_resets_on_new_month():
    p = _peak()
    p.observe(T0, 5000.0)
    january = 1_704_067_200      # 2024-01-01
    february = 1_706_745_600     # 2024-02-01
    p.observe(january, 5000.0)
    p.observe(february, 900.0)
    assert round(p.peak_w) == 900


def test_effective_kw_uses_floor_and_manual_override():
    p = _peak()
    p.observe(T0, 1000.0)
    assert p.effective_kw() == 2.5      # below the billing floor
    p.manual_kw = 6.0
    assert p.effective_kw() == 6.0      # override wins


def test_corrupt_state_file_starts_clean():
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    with open(path, "w") as fh:
        fh.write("{not json")
    p = MonthPeak(path).load()
    assert p.peak_w == 0.0
