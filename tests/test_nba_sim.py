import statistics
import unittest

from nbamodel import config, sim


class NbaSimTests(unittest.TestCase):
    def test_deterministic_and_calibrated(self):
        a = sim.run(3.0, 228.0, 100.0, [], n=3000, seed="g1")
        b = sim.run(3.0, 228.0, 100.0, [], n=3000, seed="g1")
        self.assertEqual(a["margins"], b["margins"])
        self.assertAlmostEqual(statistics.mean(a["margins"]), 3.0, delta=0.6)
        self.assertAlmostEqual(statistics.mean(a["totals"]), 228.0, delta=0.8)
        self.assertAlmostEqual(statistics.pstdev(a["margins"]), config.MARGIN_SD, delta=0.8)
        self.assertAlmostEqual(statistics.pstdev(a["totals"]), config.TOTAL_SD, delta=0.9)
        self.assertNotIn(0, a["margins"])                          # overtime until someone wins
        self.assertTrue(0.02 < sum(a["ot"]) / 3000 < 0.08)

    def test_questionable_star_splits_the_games(self):
        roster = [{"side": "home", "name": "Star", "value": 6.0, "p_play": 0.5, "recent": 1.0}]
        res = sim.run(2.0, 226.0, 99.0, roster, n=4000, seed="g2")
        s = sim.summary(res, roster)
        self.assertAlmostEqual(sum(f[0] for f in res["played"]) / 4000, 0.5, delta=0.03)
        sc = s["scenarios"][0]
        self.assertAlmostEqual(sc["with"] - sc["without"], 6.0, delta=1.2)
        self.assertAlmostEqual(s["margin"], 2.0 - 3.0, delta=0.7)  # half the time he sits

    def test_cover_and_push_on_whole_numbers(self):
        res = sim.run(0.0, 220.0, 98.0, [], n=4000, seed="g3")
        hc, push, ac = sim.cover(res, -3.0)
        self.assertGreater(push, 0.02)
        self.assertAlmostEqual(hc + push + ac, 1.0)
        over, tpush, under = sim.over_under(res, 220.0)
        self.assertGreater(tpush, 0.005)
        shifted = sim.run(0.0, 220.0, 98.0, [], n=4000, seed="g3", margin_shift=3.0)
        self.assertGreater(sim.cover(shifted, -3.0)[0], hc)


if __name__ == "__main__":
    unittest.main()
