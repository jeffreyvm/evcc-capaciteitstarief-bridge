"""Stap 2 van de bouwvolgorde: de regelwet offline testen op echte data.

Leest de CSV van `logger.py`, draait `core.bepaal()` erover en simuleert wat de
auto dan zou hebben getrokken. Rapporteert de enige metriek die telt: de
hoogste kwartierpiek, plus wat het aan laadenergie kost.

    python3 replay.py logs/capbudget-2026-08-01.csv
    python3 replay.py logs/*.csv --marge 100 --maandpiek-start 2.5

BELANGRIJK — dit is een model, geen waarheid. Het huishoudelijk verbruik komt
uit de log (p_net - p_ev) en is dus reëel, maar de reactie van auto en evcc
wordt benaderd met een minimum, een maximum en een helling. Gebruik het om
regelwetten te *vergelijken*, niet om absolute kWh te voorspellen.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

from core import KWARTIER_SEC, Instellingen, Meting, bepaal

# --- Laadmodel --------------------------------------------------------------


@dataclass(frozen=True)
class Laadmodel:
    """Grove benadering van laadpaal + auto + evcc-regeling."""

    fasen: int = 1
    spanning: float = 230.0
    min_a: float = 6.0
    max_a: float = 32.0
    slew_w_per_s: float = 300.0
    """Hoe snel het laadvermogen een nieuw setpoint volgt."""

    @property
    def min_w(self) -> float:
        return self.fasen * self.spanning * self.min_a

    @property
    def max_w(self) -> float:
        return self.fasen * self.spanning * self.max_a

    def volgende(self, huidig_w: float, envelope_w: float, huishouden_w: float, dt: float) -> float:
        doel = envelope_w - huishouden_w
        if doel < self.min_w:
            doel = 0.0
        else:
            doel = min(doel, self.max_w)
        stap = self.slew_w_per_s * dt
        if doel > huidig_w:
            return min(doel, huidig_w + stap)
        return max(doel, huidig_w - stap)


# --- Kwartierboekhouding ----------------------------------------------------


@dataclass
class Kwartier:
    index: int
    energie_wh: float = 0.0
    gedekte_sec: float = 0.0

    @property
    def gemiddeld_w(self) -> float:
        if self.gedekte_sec <= 0:
            return 0.0
        return self.energie_wh * 3600.0 / self.gedekte_sec

    @property
    def volledig(self) -> bool:
        """Randkwartieren van de log tellen niet mee — die zijn afgekapt."""
        return self.gedekte_sec >= 0.8 * KWARTIER_SEC


class Boekhouding:
    def __init__(self) -> None:
        self.kwartieren: dict[int, Kwartier] = {}

    def tel(self, ts: float, vermogen_w: float, dt: float) -> Kwartier:
        idx = int(ts // KWARTIER_SEC)
        kw = self.kwartieren.setdefault(idx, Kwartier(index=idx))
        kw.energie_wh += max(0.0, vermogen_w) * dt / 3600.0
        kw.gedekte_sec += dt
        return kw

    def piek_w(self) -> float:
        volle = [k.gemiddeld_w for k in self.kwartieren.values() if k.volledig]
        return max(volle) if volle else 0.0

    def top(self, n: int = 5) -> list[Kwartier]:
        volle = [k for k in self.kwartieren.values() if k.volledig]
        return sorted(volle, key=lambda k: k.gemiddeld_w, reverse=True)[:n]


# --- Inlezen ----------------------------------------------------------------


@dataclass
class Regel:
    ts: float
    p_net_w: float
    p_ev_w: float
    maandpiek_kw: Optional[float]
    ev_aangesloten: bool


def _f(waarde: str) -> Optional[float]:
    if waarde in ("", "None", None):
        return None
    try:
        return float(waarde)
    except ValueError:
        return None


def lees_regels(paden: Iterable[Path]) -> Iterator[Regel]:
    for pad in paden:
        with pad.open(newline="") as fh:
            for rij in csv.DictReader(fh):
                ts = _f(rij.get("ts", ""))
                p_net = _f(rij.get("p_net_w", ""))
                p_ev = _f(rij.get("p_ev_w", ""))
                if ts is None or p_net is None:
                    continue  # zonder netvermogen valt er niets te simuleren
                yield Regel(
                    ts=ts,
                    p_net_w=p_net,
                    p_ev_w=p_ev if p_ev is not None else 0.0,
                    maandpiek_kw=_f(rij.get("maandpiek_kw", "")),
                    ev_aangesloten=rij.get("ev_aangesloten", "1") in ("1", "True", "true"),
                )


# --- Simulatie --------------------------------------------------------------


@dataclass
class Resultaat:
    piek_w: float
    geladen_kwh: float
    wijzigingen: int
    boekhouding: Boekhouding = field(repr=False, default_factory=Boekhouding)


def simuleer(
    regels: Iterable[Regel],
    inst: Instellingen,
    model: Laadmodel,
    maandpiek_start_w: float = 2500.0,
    max_gat_sec: float = 60.0,
) -> tuple[Resultaat, Resultaat]:
    """Geeft (baseline, gesimuleerd) terug.

    De baseline is gewoon wat er in de log staat; de simulatie vervangt het
    laadvermogen door wat de regelwet zou hebben toegestaan.
    """
    basis = Boekhouding()
    sim = Boekhouding()
    p_ev_sim = 0.0
    geladen_wh_basis = 0.0
    geladen_wh_sim = 0.0
    verbruikt_wh = 0.0
    huidig_kwartier: Optional[int] = None
    maandpiek_w = maandpiek_start_w
    vorige_envelope: Optional[int] = None
    wijzigingen = 0
    vorige_ts: Optional[float] = None

    for r in regels:
        if vorige_ts is None:
            vorige_ts = r.ts
            continue
        dt = r.ts - vorige_ts
        vorige_ts = r.ts
        if dt <= 0 or dt > max_gat_sec:
            continue  # gat in de log: niet interpoleren

        idx = int(r.ts // KWARTIER_SEC)
        if idx != huidig_kwartier:
            if huidig_kwartier is not None:
                vorig = sim.kwartieren.get(huidig_kwartier)
                if vorig and vorig.volledig:
                    # De maandpiek groeit mee: eenmaal betaald is gratis.
                    maandpiek_w = max(maandpiek_w, vorig.gemiddeld_w)
            huidig_kwartier = idx
            verbruikt_wh = 0.0

        besluit = bepaal(
            Meting(
                ts=r.ts,
                kwartier_verbruikt_wh=verbruikt_wh,
                maandpiek_w=maandpiek_w,
                ev_aangesloten=r.ev_aangesloten,
            ),
            inst,
        )
        if vorige_envelope is None or besluit.envelope_w != vorige_envelope:
            wijzigingen += 1
            vorige_envelope = besluit.envelope_w

        huishouden_w = r.p_net_w - r.p_ev_w
        if r.ev_aangesloten:
            p_ev_sim = model.volgende(p_ev_sim, besluit.envelope_w, huishouden_w, dt)
        else:
            p_ev_sim = 0.0

        p_net_sim = huishouden_w + p_ev_sim
        sim.tel(r.ts, p_net_sim, dt)
        basis.tel(r.ts, r.p_net_w, dt)
        verbruikt_wh += max(0.0, p_net_sim) * dt / 3600.0
        geladen_wh_sim += p_ev_sim * dt / 3600.0
        geladen_wh_basis += r.p_ev_w * dt / 3600.0

    return (
        Resultaat(basis.piek_w(), geladen_wh_basis / 1000.0, 0, basis),
        Resultaat(sim.piek_w(), geladen_wh_sim / 1000.0, wijzigingen, sim),
    )


# --- CLI --------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Replay van de capaciteitsregelwet")
    p.add_argument("logs", nargs="+", type=Path)
    p.add_argument("--marge", type=float, default=150.0)
    p.add_argument("--maandpiek-start", type=float, default=2.5, help="kW")
    p.add_argument("--fasen", type=int, default=1)
    p.add_argument("--max-a", type=float, default=32.0)
    p.add_argument("--slew", type=float, default=300.0, help="W/s")
    a = p.parse_args(argv)

    inst = Instellingen(marge_w=a.marge)
    model = Laadmodel(fasen=a.fasen, max_a=a.max_a, slew_w_per_s=a.slew)

    basis, sim = simuleer(
        lees_regels(a.logs), inst, model, maandpiek_start_w=a.maandpiek_start * 1000.0
    )

    if not sim.boekhouding.kwartieren:
        print("geen bruikbare data in de log", file=sys.stderr)
        return 1

    print(f"kwartieren met volledige dekking : {sum(1 for k in sim.boekhouding.kwartieren.values() if k.volledig)}")
    print()
    print(f"{'':<22}{'gemeten':>12}{'gesimuleerd':>14}")
    print(f"{'hoogste kwartierpiek':<22}{basis.piek_w:>10.0f} W{sim.piek_w:>12.0f} W")
    print(f"{'geladen':<22}{basis.geladen_kwh:>10.2f} kWh{sim.geladen_kwh:>10.2f} kWh")
    print(f"{'envelope-wijzigingen':<22}{'-':>12}{sim.wijzigingen:>14}")
    print()
    print("hoogste kwartieren (gesimuleerd):")
    for k in sim.boekhouding.top(5):
        print(f"  index {k.index}  {k.gemiddeld_w:7.0f} W   {k.energie_wh:6.1f} Wh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
