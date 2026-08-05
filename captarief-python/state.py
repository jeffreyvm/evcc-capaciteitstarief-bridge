"""Gedeelde toestand tussen `Loop.tik()` en het dashboard.

Losse module zodat `web.py` niets van MQTT of HA hoeft te weten, en
`daemon.py` niets van FastAPI. Eén lock, één snapshot.
"""

from __future__ import annotations

import threading
import time


class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict = {"gestart_ts": time.time(), "laatste_tik_ts": None}

    def update(self, besluit_dict: dict, gepubliceerd: bool) -> None:
        with self._lock:
            self._data = {
                "gestart_ts": self._data.get("gestart_ts"),
                "laatste_tik_ts": time.time(),
                "gepubliceerd": gepubliceerd,
                **besluit_dict,
            }

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._data)