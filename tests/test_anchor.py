import unittest

from nbamodel import model as nbamodel
from nflmodel import margins
from sportscommon import anchor, totals


class AnchorTests(unittest.TestCase):
    def test_fair_moves_part_way_and_caps(self):
        self.assertAlmostEqual(anchor.fair(10.0, 7.0, 0.15, 1.5), 7.45)
        self.assertAlmostEqual(anchor.fair(30.0, 7.0, 0.15, 1.5), 8.5)   # capped
        self.assertAlmostEqual(anchor.fair(-30.0, 7.0, 0.15, 1.5), 5.5)
        self.assertEqual(anchor.fair(3.0, 7.0, 0.0, 1.5), 7.0)            # weight 0: the market

    def test_ev_tiers(self):
        self.assertAlmostEqual(anchor.ev(0.5238), 0.0, places=3)          # -110 breakeven
        self.assertEqual(anchor.tier(-0.01), "pass")
        self.assertEqual(anchor.tier(0.0), "pass")
        self.assertEqual(anchor.tier(0.005), "lean")
        self.assertEqual(anchor.tier(0.02), "play")
        self.assertEqual(anchor.tier(0.05), "strong")
        self.assertEqual(anchor.tier(None), "pass")

    def test_anchored_totals(self):
        t = totals.make_pick(52.0, 48.5, 13.5, 2, 3.5, 5, anchor=(0.15, 1.5))
        self.assertEqual((t["side"], t["edge"], t["fair"]), ("over", 3.5, 49.0))
        self.assertLess(t["prob"], 0.52)
        self.assertEqual(t["tier"], "pass")
        big = totals.make_pick(70.0, 48.5, 13.5, 2, 3.5, 5, anchor=(0.15, 1.5))
        self.assertEqual((big["fair"], big["tier"]), (50.0, "strong"))   # 1.5 pts / SD 13.5 -> 54.4%

    def test_nfl_market_mean_is_a_coin_flip(self):
        w = {k: (3.0 if k in (3, 7) else 1.0) for k in range(0, 60)}
        for s in (-8.5, -3.0, -2.5, 6.5):
            hc, _, ac = margins.cover_probs(margins.pmf(margins.market_mean(s, w), w), s)
            self.assertAlmostEqual(hc, ac, places=3)

    def test_nba_spread_priced_at_anchored_margin(self):
        proj = {"margin": 8.0, "diffs": {"eff": 8.0, "pts": 8.0, "elo": 8.0}, "hflag": 1, "offset": 0.0}
        pick, _ = nbamodel.ats_pick(proj, -2.0, lambda t: t, "H", "A")
        self.assertEqual((pick["ats_side"], pick["edge"], pick["fair_line"]), ("home", 6.0, -2.9))
        self.assertAlmostEqual(pick["cover_prob"], 0.53, places=2)       # not the unanchored 69%
        self.assertEqual(pick["tier"], "lean")


if __name__ == "__main__":
    unittest.main()
