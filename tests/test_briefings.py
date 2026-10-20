import unittest
from datetime import datetime, timezone
from unittest import mock

from cfbmodel import briefing as cfbb
from nflmodel import pipeline as nflp
from nhlmodel import pipeline as nhlp
from sportscommon import briefings
from tests import test_nfl, test_nhl
from tests.fake_nfl import build as build_nfl
from tests.fake_nhl import build as build_nhl


class BriefingTests(unittest.TestCase):
    def test_nfl_briefing_on_game_day(self):
        with test_nfl.NflDirs() as d:
            games, loader = build_nfl()
            nflp.refresh(games=games, pbp_loader=loader, now=test_nfl.NOW, log=lambda *_: None)
            game_day = datetime(2026, 10, 15, 14, 7, tzinfo=timezone.utc)  # 9:07 CT on the synthetic week-6 date
            with mock.patch.object(cfbb, "send_ntfy", return_value=True) as send:
                body = briefings.send("nfl", now=game_day)
            self.assertIn("NFL", body)
            self.assertIn("Picks", body)
            self.assertEqual(send.call_count, 1)
            self.assertIsNone(briefings.send("nfl", now=game_day.replace(hour=15)))  # once per day

    def test_nhl_briefing_and_combined_check(self):
        with test_nhl.NhlDirs():
            fx = build_nhl()
            nhlp.refresh(loader=lambda now, log: fx, now=test_nhl.NOW, log=lambda *_: None)
            when = datetime(2026, 11, 10, 15, 7, tzinfo=timezone.utc)  # 9:07 CST
            with mock.patch.object(cfbb, "check", return_value=False):
                flags = briefings.check(now=when)
            self.assertTrue(flags["nhl"])
            self.assertFalse(flags["cfb"])
            body = briefings.build_nhl(briefings._local_now(when))
            self.assertIn("Moneyline value", body)

    def test_nba_briefing(self):
        from nbamodel import pipeline as nbap
        from tests import test_nba
        from tests.fake_nba import FakeESPN
        with test_nba.NbaDirs():
            fake = FakeESPN(today=test_nba.TODAY)
            nbap.refresh(loader=test_nba.loader(fake), now=test_nba.NOW, log=lambda *_: None)
            when = datetime(2026, 10, 30, 14, 7, tzinfo=timezone.utc)  # 9:07 CDT
            with mock.patch.object(cfbb, "check", return_value=False):
                flags = briefings.check(now=when)
            self.assertTrue(flags["nba"])
            body = briefings.build_nba(briefings._local_now(when))
            self.assertIn("NBA", body)
            self.assertIn("Spread picks", body)


if __name__ == "__main__":
    unittest.main()


class ClvTests(unittest.TestCase):
    def test_spread_and_moneyline_clv(self):
        from sportscommon import clv
        self.assertEqual(clv.spread_clv("home", -3, -5), 2.0)    # laid 3, closed 5
        self.assertEqual(clv.spread_clv("away", -3, -5), -2.0)   # took +3, closed +5
        self.assertAlmostEqual(clv.ml_clv("home", 120, -110, -110), 0.1, places=3)

    def test_bet_number_held_until_side_flips(self):
        from sportscommon import clv
        p1 = clv.carry_bet({"ats_side": "home"}, None, "t1", -3)
        p2 = clv.carry_bet({"ats_side": "home"}, p1, "t2", -4.5)
        self.assertEqual((p2["bet_line"], p2["bet_at"]), (-3, "t1"))
        p3 = clv.carry_bet({"ats_side": "away"}, p2, "t3", -6)
        self.assertEqual((p3["bet_line"], p3["bet_at"]), (-6, "t3"))
        legacy = clv.carry_bet({"ats_side": "home"}, {"ats_side": "home"}, "t4", -5, first_seen=("t0", -2.5))
        self.assertEqual((legacy["bet_line"], legacy["bet_src"]), (-2.5, "first seen"))

    def test_finalize_skips_backtests(self):
        from sportscommon import clv
        p = clv.finalize_spread({"ats_side": "home", "bet_line": -3, "line": -4, "backtest": True})
        self.assertNotIn("clv", p)
        q = clv.finalize_spread({"ats_side": "home", "bet_line": -3, "line": -4})
        self.assertEqual(q["clv"], 1.0)


class TotalsTests(unittest.TestCase):
    def test_make_pick_and_grade(self):
        from sportscommon import totals
        t = totals.make_pick(52.4, 48.5, 13.5, 2, 3.5, 5)
        self.assertEqual((t["side"], t["edge"], t["tier"], t["play"]), ("over", 3.9, "play", True))
        self.assertGreater(t["prob"], 0.5)
        self.assertEqual(totals.grade(t, 49), "W")
        self.assertEqual(totals.grade(t, 48), "L")
        self.assertEqual(totals.grade(dict(t, line=48), 48), "P")
        u = totals.make_pick(44.0, 45.5, 13.5, 2, 3.5, 5)
        self.assertEqual((u["side"], u["tier"]), ("under", "pass"))
        self.assertIsNone(totals.make_pick(None, 45.5, 13.5, 2, 3.5, 5))
