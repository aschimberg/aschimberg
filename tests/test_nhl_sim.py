import unittest

from nhlmodel import sim


class NhlSimTests(unittest.TestCase):
    def test_league_average_game_matches_2025_26(self):
        # league-average regulation (non-empty-net) goals in 2025-26
        d = sim.distribution(2.87, 2.75)
        self.assertAlmostEqual(sum(d.values()), 1.0, places=3)
        mk = sim.markets(d, 5.5)
        self.assertAlmostEqual(mk["exp_home"] + mk["exp_away"], 6.25, delta=0.05)  # actual 6.25
        self.assertAlmostEqual(mk["home_m15"], 0.30, delta=0.015)                   # actual 0.301
        self.assertAlmostEqual(mk["over"], 0.57, delta=0.015)                       # actual 0.573
        one_goal = sum(p for (h, a), p in d.items() if abs(h - a) == 1)
        self.assertAlmostEqual(one_goal, 0.43, delta=0.02)                          # actual 0.432 (OT + one-goal regulation)
        self.assertFalse(any(h == a for h, a in d))                                 # no final ties

    def test_symmetric_and_expected_goals_preserved(self):
        d = sim.distribution(3.0, 3.0)
        mk = sim.markets(d)
        self.assertAlmostEqual(mk["home_win"], 0.5, places=6)
        self.assertAlmostEqual(mk["home_m15"], mk["away_m15"], places=6)
        reg = sum(p * (h + a) for h, a, p in sim._regulation(3.0, 3.0))
        self.assertLess(reg, 6.0)  # the reweighting alone lowers it; distribution() rescales back


if __name__ == "__main__":
    unittest.main()
