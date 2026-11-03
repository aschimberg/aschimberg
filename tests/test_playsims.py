import statistics
import unittest

from sportscommon import footsim, hoopsim


class PlaySimTests(unittest.TestCase):
    def test_football_means_match_projection(self):
        for lg, m, t in (("nfl", 6.0, 46.0), ("cfb", 14.0, 58.0)):
            r = footsim.run_at(lg, m, t, n=6000, seed="t")
            self.assertAlmostEqual(statistics.mean(r["margins"]), m, delta=1.0)
            self.assertAlmostEqual(statistics.mean(r["totals"]), t, delta=1.2)
            self.assertEqual(r, footsim.run_at(lg, m, t, n=6000, seed="t"))  # deterministic

    def test_nfl_key_numbers(self):
        r = footsim.run_at("nfl", 2.5, 44.0, n=6000, seed="k")
        share = lambda k: sum(1 for x in r["margins"] if abs(x) == k) / len(r["margins"])
        self.assertGreater(share(3), share(2) * 2)
        self.assertGreater(share(7), share(5) * 1.5)

    def test_basketball_means_and_spread(self):
        r = hoopsim.run("cbb", 75.0, 68.0, 68.0, 6000, seed=3)
        self.assertAlmostEqual(statistics.mean(r["margins"]), 7.0, delta=1.0)
        self.assertAlmostEqual(statistics.mean(r["totals"]), 143.0, delta=1.5)
        self.assertAlmostEqual(statistics.pstdev(r["margins"]), 11.3, delta=1.0)
        self.assertTrue(all(m != 0 for m in r["margins"]))  # overtime until someone wins


if __name__ == "__main__":
    unittest.main()
