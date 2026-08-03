"""Tests voor het laadmodel en de kwartierboekhouding."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import KWARTIER_SEC  # noqa: E402
from replay import Boekhouding, Kwartier, Laadmodel  # noqa: E402


class TestLaadmodel(unittest.TestCase):
    def setUp(self):
        self.m = Laadmodel(slew_w_per_s=10_000)  # slew uit voor deze tests

    def test_grenzen(self):
        self.assertAlmostEqual(self.m.min_w, 1380.0)
        self.assertAlmostEqual(self.m.max_w, 7360.0)

    def test_onder_minimum_is_uit(self):
        """Envelope 2350, huishouden 1500 -> 850 W over: te weinig voor 6A."""
        self.assertEqual(self.m.volgende(0, 2350, 1500, 1.0), 0.0)

    def test_boven_maximum_geklemd(self):
        self.assertAlmostEqual(self.m.volgende(0, 9000, 200, 1.0), 7360.0)

    def test_slew_begrenst_stijging(self):
        traag = Laadmodel(slew_w_per_s=100)
        self.assertAlmostEqual(traag.volgende(1400, 5000, 200, 2.0), 1600.0)

    def test_slew_begrenst_daling(self):
        traag = Laadmodel(slew_w_per_s=100)
        self.assertAlmostEqual(traag.volgende(3000, 1000, 200, 2.0), 2800.0)


class TestBoekhouding(unittest.TestCase):
    def test_gemiddelde_over_dekking(self):
        b = Boekhouding()
        ts = 1_800_000_000
        for i in range(0, 900, 10):
            b.tel(ts + i, 2000.0, 10.0)
        kw = list(b.kwartieren.values())[0]
        self.assertTrue(kw.volledig)
        self.assertAlmostEqual(kw.gemiddeld_w, 2000.0, places=3)

    def test_injectie_telt_niet_mee(self):
        b = Boekhouding()
        kw = b.tel(1_800_000_000, -3000.0, 10.0)
        self.assertEqual(kw.energie_wh, 0.0)

    def test_afgekapt_kwartier_telt_niet_als_piek(self):
        b = Boekhouding()
        b.tel(1_800_000_000, 9000.0, 30.0)  # slechts 30 s dekking
        self.assertEqual(b.piek_w(), 0.0)

    def test_kwartiergrens_splitst(self):
        b = Boekhouding()
        basis = 1_800_000_000
        b.tel(basis + 890, 1000.0, 10.0)
        b.tel(basis + 910, 1000.0, 10.0)
        self.assertEqual(len(b.kwartieren), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
