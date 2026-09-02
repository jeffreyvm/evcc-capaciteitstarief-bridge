"""Configuration, entirely from environment variables.

Read once at startup by __main__ and passed down explicitly. Nothing in the
package reads os.environ outside this module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict


def _b(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _s(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _windows(name: str, default: str) -> tuple[tuple[int, int], ...]:
    """Parse "07:00-09:00,17:00-21:00" into seconds-since-midnight pairs."""
    out = []
    for part in _s(name, default).split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        out.append((_hhmm(a), _hhmm(b)))
    return tuple(out)


def _hhmm(v: str) -> int:
    h, _, m = v.strip().partition(":")
    return int(h) * 3600 + int(m or 0) * 60


@dataclass
class Config:
    # --- evcc ---
    evcc_url: str = field(default_factory=lambda: _s("EVCC_URL", "http://127.0.0.1:7070"))
    evcc_api_key: str = field(default_factory=lambda: _s("EVCC_API_KEY", ""))
    loadpoint_id: int = field(default_factory=lambda: _i("LOADPOINT_ID", 1))

    # --- safety ---
    dry_run: bool = field(default_factory=lambda: _b("DRY_RUN", True))
    interval_s: int = field(default_factory=lambda: _i("INTERVAL_S", 15))

    # --- tariff / grid ---
    target_peak_kw: float = field(default_factory=lambda: _f("TARGET_PEAK_KW", 0.0))
    grid_phases: int = field(default_factory=lambda: _i("GRID_PHASES", 1))
    grid_voltage: int = field(default_factory=lambda: _i("GRID_VOLTAGE", 230))

    # --- charger ---
    min_current_a: int = field(default_factory=lambda: _i("MIN_CURRENT_A", 6))
    max_current_a: int = field(default_factory=lambda: _i("MAX_CURRENT_A", 32))

    # --- battery orchestration ---
    battery_control: bool = field(default_factory=lambda: _b("BATTERY_CONTROL", True))
    reserve_soc: int = field(default_factory=lambda: _i("RESERVE_SOC", 40))
    target_soc: int = field(default_factory=lambda: _i("TARGET_SOC", 80))
    hard_floor_soc: int = field(default_factory=lambda: _i("HARD_FLOOR_SOC", 10))
    precharge_lead_s: int = field(default_factory=lambda: _i("PRECHARGE_LEAD_MIN", 120) * 60)
    battery_max_charge_w: float = field(default_factory=lambda: _f("BATTERY_MAX_CHARGE_W", 2400))
    peak_windows: tuple = field(
        default_factory=lambda: _windows("PEAK_WINDOWS", "07:00-09:00,17:00-21:00"))

    # --- web ---
    web_host: str = field(default_factory=lambda: _s("WEB_HOST", "0.0.0.0"))
    web_port: int = field(default_factory=lambda: _i("WEB_PORT", 8099))

    # --- storage ---
    state_file: str = field(default_factory=lambda: _s("STATE_FILE", "/var/lib/capaciteit/state.json"))
    log_level: str = field(default_factory=lambda: _s("LOG_LEVEL", "INFO"))

    def public(self) -> dict:
        """Everything the dashboard may see — the API key never leaves here."""
        d = asdict(self)
        d.pop("evcc_api_key", None)
        d["peak_windows"] = [list(w) for w in self.peak_windows]
        return d
