"""evcc REST client. The only module in the package that talks to evcc.

Endpoints used (evcc REST API):
    GET    /api/state                              full site + loadpoint state
    GET    /api/health                             liveness
    POST   /api/loadpoints/{id}/maxcurrent/{a}     charge current setpoint
    POST   /api/batterymode/{mode}                 external battery control
    DELETE /api/batterymode                        hand control back to evcc

External battery control in evcc is watchdog-guarded: the mode must be
re-asserted regularly or evcc reverts to its own logic. The control loop runs
every 15 s and re-asserts on every pass, which is exactly the intended usage
and also means a crashed container fails safe — evcc simply takes over again.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

try:                      # aiohttp is only needed to actually talk to evcc;
    import aiohttp        # extract() and everything downstream of it is pure,
except ImportError:       # so the logic stays importable (and testable)
    aiohttp = None        # without the dependency present.

log = logging.getLogger(__name__)


class EvccError(RuntimeError):
    pass


class EvccClient:
    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = 10.0):
        if aiohttp is None:
            raise EvccError("aiohttp is required to talk to evcc: pip install aiohttp")
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self._timeout_s = timeout_s
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "EvccClient":
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout_s), headers=headers)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session:
            await self._session.close()

    async def _request(self, method: str, path: str) -> Any:
        if self._session is None:
            raise EvccError("client not started")
        url = f"{self.base}/api{path}"
        async with self._session.request(method, url) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:200]
                raise EvccError(f"{method} {path} -> {resp.status}: {body}")
            if resp.content_type == "application/json":
                data = await resp.json()
                return data.get("result", data) if isinstance(data, dict) else data
            return await resp.text()

    # --- reads ---

    async def health(self) -> bool:
        try:
            await self._request("GET", "/health")
            return True
        except Exception:
            return False

    async def state(self) -> dict:
        data = await self._request("GET", "/state")
        if not isinstance(data, dict):
            raise EvccError("unexpected /api/state payload")
        return data

    # --- writes ---

    async def set_max_current(self, loadpoint_id: int, amps: int) -> None:
        await self._request("POST", f"/loadpoints/{loadpoint_id}/maxcurrent/{amps}")

    async def set_battery_mode(self, mode: str) -> None:
        if mode not in ("normal", "hold", "charge"):
            raise ValueError(f"invalid battery mode: {mode}")
        await self._request("POST", f"/batterymode/{mode}")

    async def clear_battery_mode(self) -> None:
        """Hand battery control back to evcc — used on shutdown."""
        await self._request("DELETE", "/batterymode")


# --- state extraction (pure; tested without a network) -----------------------

def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def extract(state: dict, loadpoint_id: int) -> dict:
    """Flatten the parts of /api/state this controller cares about.

    evcc's state schema carries no compatibility promise, so every lookup is
    defensive and every value has a fallback. A renamed field degrades one
    reading rather than crashing the loop.
    """
    grid = state.get("grid") or {}
    grid_w = _num(grid.get("power"), _num(state.get("gridPower")))

    battery = state.get("battery") or []
    if isinstance(battery, dict):
        battery = [battery]
    soc = _num(state.get("batterySoc"))
    if not battery and soc == 0:
        soc = 0.0
    battery_w = _num(state.get("batteryPower"))
    capacity = sum(_num(b.get("capacity")) for b in battery if isinstance(b, dict))
    if soc == 0 and battery:
        socs = [_num(b.get("soc")) for b in battery if isinstance(b, dict)]
        soc = sum(socs) / len(socs) if socs else 0.0

    loadpoints = state.get("loadpoints") or []
    idx = loadpoint_id - 1
    lp = loadpoints[idx] if 0 <= idx < len(loadpoints) else {}
    if not isinstance(lp, dict):
        lp = {}

    return {
        "grid_w": grid_w,
        "pv_w": _num(state.get("pvPower")),
        "home_w": _num(state.get("homePower")),
        "battery_w": battery_w,
        "battery_soc": soc,
        "battery_capacity_kwh": capacity,
        "battery_mode": state.get("batteryMode") or "unknown",
        "charger_w": _num(lp.get("chargePower")),
        "charge_current_a": _num(lp.get("chargeCurrent")),
        "max_current_a": int(_num(lp.get("maxCurrent"), 32)),
        "min_current_a": int(_num(lp.get("minCurrent"), 6)),
        "phases": int(_num(lp.get("chargerPhases"), _num(lp.get("phasesActive"), 1)) or 1),
        "connected": bool(lp.get("connected")),
        "charging": bool(lp.get("charging")),
        "mode": lp.get("mode") or "unknown",
        "loadpoint_title": lp.get("title") or f"Loadpoint {loadpoint_id}",
    }
