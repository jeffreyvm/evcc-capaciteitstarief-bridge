"""Stap 1 van de bouwvolgorde: meten, zonder ook maar iets te regelen.

Schrijft een CSV per dag die de replay-harness kan lezen. Dit proces raakt
niets aan: geen MQTT, geen laadpaal, geen service calls. Je kunt het naast de
bestaande v8-opstelling laten draaien.

    HA_URL=... HA_TOKEN=... LOG_DIR=/var/lib/capbudget python3 logger.py

Standaard elke 2 s. Voor de batterijanalyse (laag 3) is snellere sampling
zinvol; wijs `ent_p_net` dan aan op een HomeWizard-sensor met korte
update-interval, of poll de P1 lokaal in plaats van via HA.
"""

from __future__ import annotations

import csv
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from bronnen import LOG_KOLOMMEN, Config, HABron

log = logging.getLogger("capbudget.logger")


class CSVSchrijver:
    """Eén bestand per UTC-dag, headers automatisch, flush na elke regel."""

    def __init__(self, map_pad: Path) -> None:
        self._map = map_pad
        self._map.mkdir(parents=True, exist_ok=True)
        self._dag: str | None = None
        self._fh = None
        self._writer: csv.DictWriter | None = None

    def _rol(self, ts: float) -> None:
        dag = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        if dag == self._dag:
            return
        if self._fh:
            self._fh.close()
        pad = self._map / f"capbudget-{dag}.csv"
        nieuw = not pad.exists()
        self._fh = pad.open("a", newline="")
        self._writer = csv.DictWriter(self._fh, fieldnames=LOG_KOLOMMEN)
        if nieuw:
            self._writer.writeheader()
        self._dag = dag
        log.info("schrijft naar %s", pad)

    def schrijf(self, regel: dict) -> None:
        self._rol(regel["ts"])
        assert self._writer is not None and self._fh is not None
        self._writer.writerow(regel)
        self._fh.flush()

    def sluit(self) -> None:
        if self._fh:
            self._fh.close()


class LoggerLoop:
    def __init__(self, cfg: Config, map_pad: Path, interval: float) -> None:
        self._bron = HABron(cfg)
        self._schrijver = CSVSchrijver(map_pad)
        self._interval = interval
        self._draait = True

    def stop(self, *_args) -> None:
        self._draait = False

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info("logger gestart, interval %.1fs", self._interval)
        while self._draait:
            begin = time.time()
            try:
                self._schrijver.schrijf(self._bron.lees_logregel(begin))
            except Exception:  # noqa: BLE001
                log.exception("fout tijdens loggen")
            # Drift corrigeren: slaap wat er van het interval overblijft.
            rest = self._interval - (time.time() - begin)
            if rest > 0:
                time.sleep(rest)
        self._schrijver.sluit()
        log.info("logger gestopt")


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOGLEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = Config()
    if not cfg.ha_token:
        log.error("HA_TOKEN ontbreekt")
        return 1
    map_pad = Path(os.getenv("LOG_DIR", "./logs"))
    interval = float(os.getenv("LOG_INTERVAL_SEC", "2"))
    LoggerLoop(cfg, map_pad, interval).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
