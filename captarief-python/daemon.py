"""Laag 1 — I/O-schil rond de regelwet.

Leest de meetlaag uit Home Assistant, roept `core.bepaal()` aan en publiceert
één getal naar MQTT. Meer doet dit proces niet: het schrijft nooit rechtstreeks
naar de laadpaal, kent geen ampere en heeft geen weet van fasen.

Twee failsafes die niet weggelaten mogen worden:
  1. `geldig=False` bij stale of onbeschikbare sensoren -> terugval op 2350 W.
  2. Stopt dit proces, dan verloopt de MQTT-waarde en valt evcc terug op de
     statische `maxPower` in evcc.yaml. Nooit op de laatst bekende waarde.

Afhankelijkheden: paho-mqtt. De rest is stdlib.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt
import uvicorn

from bronnen import Config, HABron
from core import Instellingen, Publicatiepoort, bepaal
from state import SharedState
from web import maak_app

log = logging.getLogger("capbudget")


class MQTTUitgang:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if cfg.mqtt_user:
            self._client.username_pw_set(cfg.mqtt_user, cfg.mqtt_pass)
        self._client.connect_async(cfg.mqtt_host, cfg.mqtt_port, keepalive=60)
        self._client.loop_start()

    def publiceer(self, envelope_w: int, status: dict) -> bool:
        # Niet retained: een retained envelope zou na een crash blijven staan
        # en precies de failsafe ondermijnen die we willen.
        info = self._client.publish(self._cfg.topic_envelope, str(envelope_w), qos=1)
        self._client.publish(self._cfg.topic_status, json.dumps(status), qos=0)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


class Loop:
    def __init__(self, cfg: Config, inst: Instellingen, state: SharedState) -> None:
        self._cfg = cfg
        self._inst = inst
        self._bron = HABron(cfg)
        self._uit = MQTTUitgang(cfg)
        self._poort = Publicatiepoort(inst)
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

        if self._poort.moet_publiceren(besluit, nu):
            if self._uit.publiceer(besluit.envelope_w, status):
                self._poort.bevestig(besluit, nu)
                self._state.update(status, gepubliceerd=True)
                log.info("envelope %d W - %s", besluit.envelope_w, besluit.reden)
            else:
                self._state.update(status, gepubliceerd=False)
                log.error("publiceren mislukt, poort niet bevestigd")
        else:
            self._state.update(status, gepubliceerd=False)

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info("budgetteur gestart, interval %.0fs", self._cfg.interval_sec)
        while self._draait:
            try:
                self.tik()
            except Exception:  # noqa: BLE001 - de lus mag nooit sterven
                log.exception("onverwachte fout in tik()")
            time.sleep(self._cfg.interval_sec)
        self._uit.stop()
        log.info("budgetteur gestopt - evcc valt terug op statische maxPower")


def start_dashboard(cfg: Config, state: SharedState) -> None:
    """Draait het alleen-lezen dashboard in een achtergrondthread.

    Los proces zou ook kunnen, maar dan moet `SharedState` via een socket of
    bestand gedeeld worden voor iets dat alleen `bepaal()`'s laatste uitkomst
    toont. Eén thread, geen extra transportlaag.
    """
    config = uvicorn.Config(
        maak_app(state),
        host=cfg.web_host,
        port=cfg.web_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="dashboard", daemon=True)
    thread.start()
    log.info("dashboard beschikbaar op http://%s:%d", cfg.web_host, cfg.web_port)


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
    start_dashboard(cfg, state)
    Loop(cfg, Instellingen(), state).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
