"""The control loop: read evcc, decide, apply, record.

All arithmetic lives in logic.py, battery.py and peak.py. This module only
sequences them and owns the side effects, so anything worth testing is testable
without a running evcc.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from capaciteit import battery as bat
from capaciteit import logic, peak
from capaciteit.config import Config
from capaciteit.evcc import EvccError, extract

if TYPE_CHECKING:  # avoids a hard aiohttp dependency for the pure sequencing
    from capaciteit.evcc import EvccClient
from capaciteit.store import Store

log = logging.getLogger(__name__)


def seconds_since_midnight(ts: float) -> int:
    d = datetime.fromtimestamp(ts)
    return d.hour * 3600 + d.minute * 60 + d.second


class Controller:
    def __init__(self, cfg: Config, client: "EvccClient", store: Store):
        self.cfg = cfg
        self.client = client
        self.store = store
        self.quarter = peak.QuarterTracker()
        self.month = peak.MonthPeak(cfg.state_file, manual_kw=cfg.target_peak_kw).load()
        self.released = False
        self.applied_setpoint_a: int | None = None
        self.applied_battery_mode: str | None = None

    # --- one pass -----------------------------------------------------------

    async def tick(self, ts: float | None = None) -> dict:
        ts = ts or time.time()
        raw = await self.client.state()
        s = extract(raw, self.cfg.loadpoint_id)

        # accumulate the quarter, then close out the month peak if it rolled
        self.quarter.sample(ts, s["grid_w"])
        if self.quarter.closed_avg_w is not None:
            if self.month.observe(ts, self.quarter.closed_avg_w):
                log.info("month peak now %.0f W", self.month.peak_w)

        st = self.store
        st.grid_60s.add(ts, s["grid_w"])
        st.charger_60s.add(ts, s["charger_w"])
        st.battery_60s.add(ts, s["battery_w"])

        eff_kw = self.month.effective_kw()
        remaining = self.quarter.remaining(ts)
        allowed = peak.allowed_power_w(eff_kw * 1000, self.quarter.consumed_wh, remaining)
        projected = self.quarter.projected_w(ts, st.grid_60s.mean(s["grid_w"]))

        ev_decision = logic.decide(logic.Snapshot(
            grid_w=st.grid_60s.mean(s["grid_w"]),
            charger_w=st.charger_60s.mean(s["charger_w"]),
            battery_net_w=st.battery_60s.mean(s["battery_w"]),
            grid_instant_w=s["grid_w"],
            allowed_power_w=allowed,
            remaining_time_s=remaining,
            battery_discharge_w=self.cfg.battery_max_charge_w,
            battery_soc=s["battery_soc"],
            effective_peak_kw=eff_kw,
            charge_current_a=s["charge_current_a"],
            max_current_setpoint_a=s["max_current_a"],
            recommended_peak_60s_a=int(st.recommended_60s.max(s["max_current_a"])),
            phases=s["phases"] or self.cfg.grid_phases,
            voltage=self.cfg.grid_voltage,
            min_a=self.cfg.min_current_a,
            max_a=self.cfg.max_current_a,
        ))
        st.recommended_60s.add(ts, ev_decision.recommended_a)

        bat_decision = bat.decide_battery(bat.BatterySnapshot(
            now_s=seconds_since_midnight(ts),
            grid_w=st.grid_60s.mean(s["grid_w"]),
            quarter_projected_w=projected,
            effective_peak_kw=eff_kw,
            allowed_power_w=allowed,
            battery_soc=s["battery_soc"],
            battery_max_charge_w=self.cfg.battery_max_charge_w,
            currently_released=self.released,
            reserve_soc=self.cfg.reserve_soc,
            target_soc=self.cfg.target_soc,
            hard_floor_soc=self.cfg.hard_floor_soc,
            lead_s=self.cfg.precharge_lead_s,
            windows=self.cfg.peak_windows,
        ))
        self.released = bat_decision.mode == "release"

        applied = await self.apply(ev_decision, bat_decision)

        snapshot = {
            "ts": ts,
            "dry_run": self.cfg.dry_run,
            **{k: s[k] for k in (
                "grid_w", "pv_w", "home_w", "battery_w", "battery_soc",
                "battery_mode", "charger_w", "charge_current_a", "max_current_a",
                "connected", "charging", "mode", "loadpoint_title", "phases")},
            "quarter": {
                "elapsed_s": self.quarter.elapsed(ts),
                "remaining_s": remaining,
                "consumed_wh": self.quarter.consumed_wh,
                "budget_wh": eff_kw * 1000 * peak.QUARTER_S / 3600,
            },
            "projected_w": projected,
            "allowed_w": allowed,
            "month_peak_w": self.month.peak_w,
            "effective_peak_kw": eff_kw,
            "ev": ev_decision.as_attributes(),
            "battery": bat_decision.as_attributes(),
            "applied": applied,
        }
        st.record(snapshot)
        return snapshot

    # --- side effects -------------------------------------------------------

    async def apply(self, ev, bt) -> dict:
        """Push decisions to evcc. In dry run, report intent and change nothing."""
        actions: list[str] = []

        want_current = ev.new_setpoint_a
        want_mode = bt.evcc_mode if self.cfg.battery_control else None

        if want_current is not None:
            actions.append(f"maxcurrent={want_current}A")
        if want_mode and want_mode != self.applied_battery_mode:
            actions.append(f"batterymode={want_mode}")

        if self.cfg.dry_run:
            return {"dry_run": True, "would": actions, "ok": True}

        ok, errors = True, []
        try:
            if want_current is not None:
                await self.client.set_max_current(self.cfg.loadpoint_id, want_current)
                self.applied_setpoint_a = want_current
        except EvccError as exc:
            ok = False
            errors.append(str(exc))
            self.store.note_error(f"maxcurrent: {exc}")

        try:
            if want_mode:
                # re-assert every pass: evcc's external battery control is
                # watchdog-guarded and reverts if we go quiet
                await self.client.set_battery_mode(want_mode)
                self.applied_battery_mode = want_mode
        except EvccError as exc:
            ok = False
            errors.append(str(exc))
            self.store.note_error(f"batterymode: {exc}")

        return {"dry_run": False, "applied": actions, "ok": ok, "errors": errors}

    # --- runner -------------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        log.info("control loop start (dry_run=%s, interval=%ss)",
                 self.cfg.dry_run, self.cfg.interval_s)
        while not stop.is_set():
            began = time.time()
            try:
                await self.tick(began)
            except Exception as exc:  # never let one bad pass kill the loop
                log.warning("tick failed: %s", exc)
                self.store.note_error(str(exc))
            delay = max(1.0, self.cfg.interval_s - (time.time() - began))
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
        await self.release_control()

    async def release_control(self) -> None:
        """Hand the battery back to evcc on a clean shutdown."""
        if self.cfg.dry_run or not self.cfg.battery_control:
            return
        try:
            await self.client.clear_battery_mode()
            log.info("battery control returned to evcc")
        except Exception as exc:
            log.warning("could not clear battery mode: %s", exc)
