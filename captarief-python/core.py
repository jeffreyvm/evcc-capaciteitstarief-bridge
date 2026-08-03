"""Laag 1 — Budgetteur: pure beslislogica.

Deze module bevat GEEN I/O. Dat is opzet: de daemon (laag 1-schil) en de
replay-harness (stap 2 van de bouwvolgorde) roepen exact dezelfde functie
`bepaal()` aan. Wat je offline test is dus letterlijk wat er 's avonds draait.

Output is één getal in watt: de envelope voor het evcc-circuit. Geen ampere,
geen fasen, geen laadpaal — dat is de verantwoordelijkheid van laag 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# --- Constanten van het capaciteitstarief ----------------------------------

KWARTIER_SEC = 900.0
ONDERGRENS_W = 2500.0  # Belgische minimumdrempel: lager rekent Fluvius toch niet af


# --- Configuratie -----------------------------------------------------------


@dataclass(frozen=True)
class Instellingen:
    """Alles wat je zou willen tunen zonder de regelwet aan te raken."""

    marge_w: float = 150.0
    """Absolute veiligheidsmarge onder het doel. Absoluut, niet multiplicatief —
    dat was de v7-fout die twee keer werd toegepast."""

    piek_bovengrens_w: float = 9200.0
    """Sanity clamp. Een maandpiek boven deze waarde is vrijwel zeker een
    meetfout en mag de envelope niet meesleuren."""

    envelope_vloer_w: float = 100.0
    """Nooit 0 publiceren: sommige evcc-versies gaan raar om met maxPower=0.
    Bij deze waarde haalt de laadpaal sowieso geen 6A en stopt evcc zelf."""

    publicatie_drempel_w: float = 50.0
    """Kleinere wijzigingen dan dit niet publiceren (rust op de bus)."""

    hartslag_sec: float = 30.0
    """Ook zonder wijziging periodiek publiceren, zodat de `timeout` op
    GetMaxPower in evcc niet verloopt. Moet ruim onder die timeout liggen."""


# --- In- en uitvoer ---------------------------------------------------------


@dataclass(frozen=True)
class Meting:
    """Momentopname uit laag 0."""

    ts: float
    """Unix-tijd in seconden. Kwartiergrenzen worden hieruit afgeleid."""

    kwartier_verbruikt_wh: float
    """Netafname in dit kwartier tot nu toe (sensor.net_afname_kwartier)."""

    maandpiek_w: float
    """Hoogste kwartierpiek deze maand tot nu toe."""

    p_net_w: Optional[float] = None
    """Alleen diagnostiek — de regelwet gebruikt dit niet. De momentane
    regeling op netvermogen is het werk van evcc (laag 2)."""

    p_ev_w: Optional[float] = None
    ev_aangesloten: bool = True

    vrijgave_w: Optional[float] = None
    """Ontsnappingsklep: bewust een hogere piek accepteren ('vanavond vol')."""

    geldig: bool = True
    """False als een bronsensor unavailable/stale is."""


@dataclass(frozen=True)
class Besluit:
    envelope_w: int
    doel_w: float
    reden: str
    diagnostiek: dict = field(default_factory=dict)


# --- Hulpfuncties -----------------------------------------------------------


def resterend_kwartier_sec(ts: float) -> float:
    """Seconden tot de volgende kwartiergrens.

    Unix-tijd is op :00 uitgelijnd en de Belgische tijdzone verschilt een heel
    aantal uren van UTC, dus modulo op de epoch klopt met de meterkwartieren.
    """
    return KWARTIER_SEC - (ts % KWARTIER_SEC)


def _klem(waarde: float, laag: float, hoog: float) -> float:
    return max(laag, min(hoog, waarde))


# --- De regelwet ------------------------------------------------------------


def bepaal(m: Meting, inst: Instellingen = Instellingen()) -> Besluit:
    """Bereken de circuit-envelope voor dit moment.

    Twee regels, meer niet:

    1. Het doel is de maandpiek die je toch al betaalt (minimaal 2,5 kW).
       Headroom die je deze maand al gekocht hebt, is gratis.
    2. Correctie mag alleen naar beneden. Loopt het kwartier voor op budget,
       dan zakt de envelope; loopt het achter, dan blijft hij op het doel
       staan en klimt hij *niet* hyperbolisch mee. Dat is de v7-bug.

    Geen freeze window, geen stapgrenzen, geen noodrem: die compenseerden
    voor het feit dat Home Assistant de snelle regelaar was. Dat is nu evcc.
    """
    if not m.geldig:
        veilig = ONDERGRENS_W - inst.marge_w
        return Besluit(
            envelope_w=int(veilig),
            doel_w=veilig,
            reden="meting ongeldig — terugval op ondergrens",
            diagnostiek={"failsafe": True},
        )

    # Stap 1: doel bepalen uit de maandpiek.
    ruwe_piek = _klem(m.maandpiek_w, ONDERGRENS_W, inst.piek_bovengrens_w)
    doel = ruwe_piek - inst.marge_w

    vrijgave_actief = False
    if m.vrijgave_w is not None and m.vrijgave_w > doel:
        doel = _klem(m.vrijgave_w, doel, inst.piek_bovengrens_w)
        vrijgave_actief = True

    # Stap 2: kwartierbudget en wat daarvan over is.
    budget_wh = doel * KWARTIER_SEC / 3600.0
    rest_wh = budget_wh - m.kwartier_verbruikt_wh
    rem_sec = max(resterend_kwartier_sec(m.ts), 1.0)
    toegestaan_gemiddelde_w = rest_wh * 3600.0 / rem_sec

    # Stap 3: plafond-only. De min() is wat de hyperbolische klim tegenhoudt.
    envelope = _klem(min(doel, toegestaan_gemiddelde_w), inst.envelope_vloer_w, doel)

    if not m.ev_aangesloten:
        reden = "geen auto aangesloten — envelope informatief"
    elif vrijgave_actief:
        reden = "vrijgave actief — hogere piek bewust geaccepteerd"
    elif envelope <= inst.envelope_vloer_w:
        reden = "kwartierbudget op — laden onderbroken"
    elif toegestaan_gemiddelde_w < doel:
        reden = "voorlopend op budget — envelope verlaagd"
    else:
        reden = "vlak op doel"

    return Besluit(
        envelope_w=int(round(envelope)),
        doel_w=doel,
        reden=reden,
        diagnostiek={
            "maandpiek_w": round(ruwe_piek, 1),
            "budget_wh": round(budget_wh, 1),
            "verbruikt_wh": round(m.kwartier_verbruikt_wh, 1),
            "rest_wh": round(rest_wh, 1),
            "resterend_sec": round(rem_sec, 1),
            "toegestaan_gemiddelde_w": round(toegestaan_gemiddelde_w, 1),
            "p_net_w": m.p_net_w,
            "p_ev_w": m.p_ev_w,
        },
    )


# --- Publicatiepoort --------------------------------------------------------


class Publicatiepoort:
    """Beslist of een besluit naar MQTT moet.

    Bewust apart van `bepaal()`: dit is het enige stukje toestand in laag 1,
    en het heeft niets met de regelwet te maken.
    """

    def __init__(self, inst: Instellingen = Instellingen()) -> None:
        self._inst = inst
        self._laatste_waarde: Optional[int] = None
        self._laatste_ts: Optional[float] = None

    def moet_publiceren(self, besluit: Besluit, ts: float) -> bool:
        if self._laatste_waarde is None or self._laatste_ts is None:
            return True
        if abs(besluit.envelope_w - self._laatste_waarde) >= self._inst.publicatie_drempel_w:
            return True
        return (ts - self._laatste_ts) >= self._inst.hartslag_sec

    def bevestig(self, besluit: Besluit, ts: float) -> None:
        """Pas aanroepen nadat de publicatie geslaagd is."""
        self._laatste_waarde = besluit.envelope_w
        self._laatste_ts = ts
