"""Laag 0-lezer: haalt de meetlaag uit Home Assistant.

Apart van `daemon.py` zodat de logger dit kan gebruiken zonder paho-mqtt te
hoeven installeren, en zodat er maar één plek is waar entity-ids staan.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from core import Meting

log = logging.getLogger("capbudget.bronnen")


@dataclass(frozen=True)
class Config:
    ha_url: str = os.getenv("HA_URL", "http://homeassistant.local:8123")
    ha_token: str = os.getenv("HA_TOKEN", "")

    mqtt_host: str = os.getenv("MQTT_HOST", "127.0.0.1")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_user: str = os.getenv("MQTT_USER", "")
    mqtt_pass: str = os.getenv("MQTT_PASS", "")
    topic_envelope: str = os.getenv("TOPIC_ENVELOPE", "capaciteit/circuit/maxpower")
    topic_status: str = os.getenv("TOPIC_STATUS", "capaciteit/status")

    ent_kwartier: str = "sensor.net_afname_kwartier"
    ent_maandpiek: str = "sensor.effectieve_maandpiek"
    ent_p_net: str = "sensor.p1_meter_power"
    ent_p_ev: str = "sensor.evcc_oprit_charge_power"
    ent_verbonden: str = "binary_sensor.evcc_oprit_connected"
    ent_vrijgave: str = "input_number.cap_vrijgave_w"

    # Alleen voor de logger — de regelwet gebruikt deze niet.
    ent_bat_zolder_w: str = "sensor.indevolt_zolder_battery_power"
    ent_bat_garage_w: str = "sensor.indevolt_garage_battery_power"
    ent_soc_zolder: str = "sensor.indevolt_zolder_battery_soc"
    ent_soc_garage: str = "sensor.indevolt_garage_battery_soc"

    interval_sec: float = float(os.getenv("INTERVAL_SEC", "30"))
    max_leeftijd_sec: float = 180.0


class HABron:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg

    # -- laag niveau ---------------------------------------------------------

    def haal_state(self, entity_id: str) -> Optional[dict]:
        url = f"{self._cfg.ha_url}/api/states/{entity_id}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._cfg.ha_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log.warning("kan %s niet lezen: %s", entity_id, exc)
            return None

    def getal(self, entity_id: str, nu: float, controleer_leeftijd: bool = True) -> Optional[float]:
        data = self.haal_state(entity_id)
        if data is None:
            return None
        toestand = data.get("state")
        if toestand in (None, "unknown", "unavailable", ""):
            log.warning("%s is %s", entity_id, toestand)
            return None
        try:
            waarde = float(toestand)
        except ValueError:
            log.warning("%s is geen getal: %r", entity_id, toestand)
            return None
        if controleer_leeftijd and self._is_verouderd(data, nu):
            log.warning("%s is verouderd", entity_id)
            return None
        return waarde

    def _is_verouderd(self, data: dict, nu: float) -> bool:
        stempel = data.get("last_updated")
        if not stempel:
            return False
        try:
            gezien = datetime.fromisoformat(stempel.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return False
        return (nu - gezien) > self._cfg.max_leeftijd_sec

    # -- hoog niveau ---------------------------------------------------------

    def lees_meting(self, nu: float) -> Meting:
        cfg = self._cfg
        kwartier = self.getal(cfg.ent_kwartier, nu)
        maandpiek_kw = self.getal(cfg.ent_maandpiek, nu)
        geldig = kwartier is not None and maandpiek_kw is not None

        verbonden_data = self.haal_state(cfg.ent_verbonden)
        verbonden = (verbonden_data or {}).get("state") == "on"

        vrijgave = self.getal(cfg.ent_vrijgave, nu)
        if vrijgave is not None and vrijgave <= 0:
            vrijgave = None

        return Meting(
            ts=nu,
            kwartier_verbruikt_wh=kwartier or 0.0,
            maandpiek_w=(maandpiek_kw or 2.5) * 1000.0,  # sensor staat in kW
            p_net_w=self.getal(cfg.ent_p_net, nu),
            p_ev_w=self.getal(cfg.ent_p_ev, nu),
            ev_aangesloten=verbonden,
            vrijgave_w=vrijgave,
            geldig=geldig,
        )

    def lees_logregel(self, nu: float) -> dict:
        """Bredere momentopname voor de logger, inclusief batterijen."""
        cfg = self._cfg
        verbonden_data = self.haal_state(cfg.ent_verbonden)
        return {
            "ts": round(nu, 1),
            "p_net_w": self.getal(cfg.ent_p_net, nu, controleer_leeftijd=False),
            "p_ev_w": self.getal(cfg.ent_p_ev, nu, controleer_leeftijd=False),
            "kwartier_verbruikt_wh": self.getal(cfg.ent_kwartier, nu, False),
            "maandpiek_kw": self.getal(cfg.ent_maandpiek, nu, False),
            "ev_aangesloten": int((verbonden_data or {}).get("state") == "on"),
            "bat_zolder_w": self.getal(cfg.ent_bat_zolder_w, nu, False),
            "bat_garage_w": self.getal(cfg.ent_bat_garage_w, nu, False),
            "soc_zolder": self.getal(cfg.ent_soc_zolder, nu, False),
            "soc_garage": self.getal(cfg.ent_soc_garage, nu, False),
        }


LOG_KOLOMMEN = [
    "ts",
    "p_net_w",
    "p_ev_w",
    "kwartier_verbruikt_wh",
    "maandpiek_kw",
    "ev_aangesloten",
    "bat_zolder_w",
    "bat_garage_w",
    "soc_zolder",
    "soc_garage",
]
