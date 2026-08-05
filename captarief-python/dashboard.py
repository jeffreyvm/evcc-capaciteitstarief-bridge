"""Standalone dashboard: leest HA, rekent `bepaal()` uit, publiceert niets.

Bedoeld voor uitrolstap 1-3, voordat de daemon (die wél naar MQTT schrijft
en dus de laadpaal beinvloedt) aangezet wordt. Geen paho-mqtt-afhankelijkheid,
geen actuatiepad — alleen de HTTP-server uit `web.py` gevoed door dezelfde
`bepaal()`-functie als de daemon en de replay-harness.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time

import uvicorn

from bronnen import Config, HABron
from core import Instellingen, bepaal
from state import SharedState
from web import maak_app

log = logging.getLogger("capbudget.dashboard")


class DashboardLoop:
    def __init__(self, cfg: Config, inst: Instellingen, state: SharedState) -> None:
        self._cfg = cfg
        self._inst = inst
        self._bron = HABron(cfg)
        self._state = state
        self._draait = True

    def stop(self, *_args) -> None:
        log.info("stopsignaal ontvangen")
        self._draait = False

    def tik(self) -> None:
        nu = time.time()
        meting = self._bron.lees_meting(nu)
        besluit = bepaal(meting, self._inst)
        status = {
            "envelope_w": besluit.envelope_w,
            "doel_w": round(besluit.doel_w, 1),
            "reden": besluit.reden,
            **besluit.diagnostiek,
        }
        self._state.update(status, gepubliceerd=False)

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info("dashboard-lus gestart, interval %.0fs — publiceert niets", self._cfg.interval_sec)
        while self._draait:
            try:
                self.tik()
            except Exception:  # noqa: BLE001 - de lus mag nooit sterven
                log.exception("onverwachte fout in tik()")
            time.sleep(self._cfg.interval_sec)
        log.info("dashboard-lus gestopt")


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOGLEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = Config()
    if not cfg.ha_token:
        log.error("HA_TOKEN ontbreekt")
        return 1

    state = SharedState()
    config = uvicorn.Config(maak_app(state), host=cfg.web_host, port=cfg.web_port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="dashboard-http", daemon=True)
    thread.start()
    log.info("dashboard beschikbaar op http://%s:%d", cfg.web_host, cfg.web_port)

    DashboardLoop(cfg, Instellingen(), state).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())