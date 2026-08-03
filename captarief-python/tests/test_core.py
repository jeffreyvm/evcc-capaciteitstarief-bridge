"""Tests voor de regelwet. Alleen stdlib — draait overal, ook offline."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import (  # noqa: E402
    Besluit,
    Instellingen,
    Meting,
    Publicatiepoort,
    bepaal,
    resterend_kwartier_sec,
)

INST = Instellingen()


def ts_op(seconden_in_kwartier: float) -> float:
    """Tijdstip dat exact N seconden na een kwartiergrens ligt."""
    basis = 1_800_000_000  # deelbaar door 900
    assert basis % 900 == 0
    return basis + seconden_in_kwartier


class TestKwartierrekenen(unittest.TestCase):
    def test_grens(self):
        self.assertAlmostEqual(resterend_kwartier_sec(ts_op(0)), 900.0)
        self.assertAlmostEqual(resterend_kwartier_sec(ts_op(300)), 600.0)
        self.assertAlmostEqual(resterend_kwartier_sec(ts_op(899)), 1.0)


class TestDoelbepaling(unittest.TestCase):
    def test_begin_van_de_maand(self):
        """Zonder relevante maandpiek: 2500 - 150 marge."""
        b = bepaal(Meting(ts=ts_op(0), kwartier_verbruikt_wh=0, maandpiek_w=1200), INST)
        self.assertEqual(b.envelope_w, 2350)
        self.assertEqual(b.reden, "vlak op doel")

    def test_maandpiek_geeft_gratis_ruimte(self):
        """Kern van de herziening: de oven heeft al 4,2 kW gepiekt."""
        b = bepaal(Meting(ts=ts_op(0), kwartier_verbruikt_wh=0, maandpiek_w=4200), INST)
        self.assertEqual(b.envelope_w, 4050)

    def test_absurde_maandpiek_wordt_geklemd(self):
        b = bepaal(Meting(ts=ts_op(0), kwartier_verbruikt_wh=0, maandpiek_w=98000), INST)
        self.assertEqual(b.envelope_w, int(INST.piek_bovengrens_w - INST.marge_w))


class TestPlafondOnly(unittest.TestCase):
    def test_voorlopend_verlaagt(self):
        """Halverwege het kwartier al 400 Wh op een budget van 587 Wh."""
        b = bepaal(
            Meting(ts=ts_op(450), kwartier_verbruikt_wh=400, maandpiek_w=2500), INST
        )
        self.assertLess(b.envelope_w, 2350)
        self.assertEqual(b.reden, "voorlopend op budget — envelope verlaagd")

    def test_budget_op_stopt_laden(self):
        b = bepaal(
            Meting(ts=ts_op(700), kwartier_verbruikt_wh=900, maandpiek_w=2500), INST
        )
        self.assertEqual(b.envelope_w, int(INST.envelope_vloer_w))
        self.assertEqual(b.reden, "kwartierbudget op — laden onderbroken")

    def test_geen_hyperbolische_klim(self):
        """Regressietest op de v7-bug.

        Achterlopend op budget vlak voor de kwartiergrens: de naïeve formule
        `rest_wh * 3600 / rem_sec` geeft hier tienduizenden watt. De envelope
        mag het doel nooit overschrijden.
        """
        for rem in (600, 300, 120, 60, 20, 5, 1):
            with self.subTest(resterend=rem):
                b = bepaal(
                    Meting(
                        ts=ts_op(900 - rem),
                        kwartier_verbruikt_wh=0.0,
                        maandpiek_w=2500,
                    ),
                    INST,
                )
                self.assertEqual(b.envelope_w, 2350)
                self.assertGreater(b.diagnostiek["toegestaan_gemiddelde_w"], 2350)

    def test_envelope_daalt_monotoon_bij_constant_verbruik(self):
        """Bij verbruik boven doel moet de envelope zakken, niet oscilleren."""
        vorige = 10_000
        for sec in range(0, 900, 60):
            verbruikt = 3000 * (sec / 3600.0)  # constant 3 kW afname
            b = bepaal(
                Meting(ts=ts_op(sec), kwartier_verbruikt_wh=verbruikt, maandpiek_w=2500),
                INST,
            )
            self.assertLessEqual(b.envelope_w, vorige)
            vorige = b.envelope_w


class TestRandgevallen(unittest.TestCase):
    def test_ongeldige_meting_valt_veilig_terug(self):
        b = bepaal(
            Meting(ts=ts_op(0), kwartier_verbruikt_wh=0, maandpiek_w=6000, geldig=False),
            INST,
        )
        self.assertEqual(b.envelope_w, 2350)
        self.assertTrue(b.diagnostiek["failsafe"])

    def test_vrijgave_verhoogt_doel(self):
        b = bepaal(
            Meting(
                ts=ts_op(0),
                kwartier_verbruikt_wh=0,
                maandpiek_w=2500,
                vrijgave_w=7400,
            ),
            INST,
        )
        self.assertEqual(b.envelope_w, 7400)
        self.assertIn("vrijgave", b.reden)

    def test_vrijgave_onder_doel_wordt_genegeerd(self):
        b = bepaal(
            Meting(
                ts=ts_op(0), kwartier_verbruikt_wh=0, maandpiek_w=5000, vrijgave_w=3000
            ),
            INST,
        )
        self.assertEqual(b.envelope_w, 4850)

    def test_geen_auto(self):
        b = bepaal(
            Meting(
                ts=ts_op(0),
                kwartier_verbruikt_wh=0,
                maandpiek_w=2500,
                ev_aangesloten=False,
            ),
            INST,
        )
        self.assertIn("geen auto", b.reden)


class TestPublicatiepoort(unittest.TestCase):
    def _besluit(self, w: int) -> Besluit:
        return Besluit(envelope_w=w, doel_w=2350, reden="test")

    def test_eerste_altijd(self):
        poort = Publicatiepoort(INST)
        self.assertTrue(poort.moet_publiceren(self._besluit(2350), 0.0))

    def test_kleine_wijziging_wordt_ingehouden(self):
        poort = Publicatiepoort(INST)
        poort.bevestig(self._besluit(2350), 0.0)
        self.assertFalse(poort.moet_publiceren(self._besluit(2330), 5.0))

    def test_hartslag_dwingt_publicatie(self):
        """Cruciaal: anders verloopt de GetMaxPower-timeout in evcc."""
        poort = Publicatiepoort(INST)
        poort.bevestig(self._besluit(2350), 0.0)
        self.assertFalse(poort.moet_publiceren(self._besluit(2350), 29.0))
        self.assertTrue(poort.moet_publiceren(self._besluit(2350), 31.0))

    def test_grote_wijziging_direct(self):
        poort = Publicatiepoort(INST)
        poort.bevestig(self._besluit(2350), 0.0)
        self.assertTrue(poort.moet_publiceren(self._besluit(1000), 1.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
