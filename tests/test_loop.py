"""Controller wiring, exercised against a fake evcc client.

No network, no evcc, no container: the loop is just a sequencer, so it can be
driven with a canned /api/state payload.
"""
import asyncio
import os
import tempfile

from capaciteit.config import Config
from capaciteit.evcc import extract
from capaciteit.loop import Controller, seconds_since_midnight
from capaciteit.peak import QUARTER_S
from capaciteit.store import Store

T0 = 1_700_000_100 - (1_700_000_100 % QUARTER_S)

STATE = {
    "grid": {"power": 4200.0},
    "pvPower": 500.0,
    "homePower": 3000.0,
    "batteryPower": 0.0,
    "batterySoc": 55.0,
    "batteryMode": "normal",
    "battery": [{"capacity": 5.0, "soc": 55.0}, {"capacity": 5.0, "soc": 55.0}],
    "loadpoints": [{
        "title": "Oprit",
        "chargePower": 3680.0,
        "chargeCurrent": 16.0,
        "maxCurrent": 16.0,
        "minCurrent": 6.0,
        "chargerPhases": 1,
        "connected": True,
        "charging": True,
        "mode": "pv",
    }],
}


class FakeClient:
    def __init__(self, state=None):
        self.state_payload = state or STATE
        self.calls = []

    async def state(self):
        return self.state_payload

    async def set_max_current(self, lp, amps):
        self.calls.append(("maxcurrent", lp, amps))

    async def set_battery_mode(self, mode):
        self.calls.append(("batterymode", mode))

    async def clear_battery_mode(self):
        self.calls.append(("clear",))


def make_config(**overrides) -> Config:
    env = {
        "STATE_FILE": os.path.join(tempfile.mkdtemp(), "state.json"),
        "DRY_RUN": "true",
        "TARGET_PEAK_KW": "5",
    }
    env.update({k: str(v) for k, v in overrides.items()})
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return Config()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --- extraction ---

def test_extract_reads_the_fields_the_loop_needs():
    s = extract(STATE, 1)
    assert s["grid_w"] == 4200.0
    assert s["battery_soc"] == 55.0
    assert s["charger_w"] == 3680.0
    assert s["max_current_a"] == 16
    assert s["loadpoint_title"] == "Oprit"


def test_extract_survives_a_renamed_field():
    s = extract({"loadpoints": []}, 1)
    assert s["grid_w"] == 0.0
    assert s["max_current_a"] == 32       # documented fallback, not a crash


def test_extract_averages_soc_when_only_per_battery_values_exist():
    state = {"battery": [{"soc": 30.0}, {"soc": 70.0}], "loadpoints": []}
    assert extract(state, 1)["battery_soc"] == 50.0


def test_seconds_since_midnight_is_local():
    assert 0 <= seconds_since_midnight(T0) < 86400


# --- the loop ---

def run(coro):
    return asyncio.run(coro)


def test_tick_produces_a_full_snapshot():
    cfg, client = make_config(), FakeClient()
    c = Controller(cfg, client, Store())
    snap = run(c.tick(T0 + 300))
    for key in ("grid_w", "projected_w", "allowed_w", "ev", "battery", "quarter"):
        assert key in snap
    assert snap["ev"]["reason"]
    assert snap["battery"]["reason"]


def test_dry_run_sends_nothing():
    cfg, client = make_config(DRY_RUN="true"), FakeClient()
    c = Controller(cfg, client, Store())
    snap = run(c.tick(T0 + 300))
    assert client.calls == []
    assert snap["applied"]["dry_run"] is True


def test_live_run_pushes_battery_mode():
    cfg, client = make_config(DRY_RUN="false"), FakeClient()
    c = Controller(cfg, client, Store())
    run(c.tick(T0 + 300))
    assert any(call[0] == "batterymode" for call in client.calls)


def test_month_peak_is_learned_from_closed_quarters():
    cfg = make_config(TARGET_PEAK_KW="0")
    c = Controller(cfg, FakeClient(), Store())
    run(c.tick(T0 + 60))
    run(c.tick(T0 + 800))
    run(c.tick(T0 + QUARTER_S + 60))     # rolls the window
    assert c.month.peak_w > 0


def test_history_is_recorded_for_the_dashboard():
    store = Store()
    c = Controller(make_config(), FakeClient(), store)
    run(c.tick(T0 + 60))
    run(c.tick(T0 + 120))
    assert len(store.history) == 2
    payload = store.payload(c.cfg.public())
    assert payload["latest"] is not None
    assert "evcc_api_key" not in payload["config"]


def test_release_state_persists_between_ticks_for_hysteresis():
    state = dict(STATE, grid={"power": 6000.0})
    c = Controller(make_config(), FakeClient(state), Store())
    run(c.tick(T0 + 60))
    assert c.released is True
