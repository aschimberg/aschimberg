import os
import unittest

os.environ.setdefault("NHL_ESPN", "off")  # tests never call ESPN
os.environ.setdefault("NHL_MONEYPUCK", "off")
from datetime import datetime, timezone
from unittest import mock

from cfbmodel import store
from nhlmodel import config, pipeline, sim
from tests import test_pipeline as tp
from tests.fake_nhl import build


class NhlDirs(tp.TempDirs):
    def __enter__(self):
        super().__enter__()
        root = self.root
        self.p2 = [
            mock.patch.object(config, "DATA_DIR", root / "nhl"),
            mock.patch.object(config, "CACHE_DIR", root / "nhl" / "cache"),
            mock.patch.object(config, "SITE_DATA_DIR", root / "site_nhl"),
            mock.patch.object(pipeline, "PICKS_PATH", root / "nhl" / "picks.json"),
            mock.patch.object(pipeline, "LINE_HISTORY_PATH", root / "nhl" / "lh.json"),
            mock.patch.dict(config.WEIGHTS, {"xg": 0.55, "goals": 0.2, "elo": 0.25}),
            mock.patch.object(config, "HOME_ICE", 0.2),
        ]
        for p in self.p2:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.p2:
            p.stop()
        super().__exit__(*exc)


NOW = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)


class NhlTests(unittest.TestCase):
    def test_distribution_properties(self):
        d = sim.distribution(3.4, 2.6)
        m = sim.markets(d, 6.5)
        self.assertAlmostEqual(sum(d.values()), 1.0, places=3)
        self.assertGreater(m["home_win"], 0.55)
        self.assertLess(m["home_m15"], m["home_win"])
        self.assertAlmostEqual(m["over"] + m["under"], 1.0, places=3)  # 6.5 can't push
        even = sim.markets(sim.distribution(3.0, 3.0))
        self.assertAlmostEqual(even["home_win"], 0.5, places=3)

    def test_parse_partner_odds(self):
        doc = {"bettingPartner": {"name": "DraftKings"}, "games": [{"gameId": 1, "homeTeam": {"odds": [
            {"description": "MONEY_LINE_2_WAY", "value": -145.0, "qualifier": ""},
            {"description": "PUCK_LINE", "value": 180.0, "qualifier": "-1.5"},
            {"description": "OVER_UNDER", "value": -110.0, "qualifier": "O6.5"}]},
            "awayTeam": {"odds": [{"description": "MONEY_LINE_2_WAY", "value": 124.0, "qualifier": ""},
                                  {"description": "OVER_UNDER", "value": -110.0, "qualifier": "U6.5"}]}}]}
        o = pipeline.parse_partner_odds(doc)[1]
        self.assertEqual((o["ml_home"], o["ml_away"], o["total"], o["pl_home"]), (-145.0, 124.0, 6.5, -1.5))

    def test_refresh_grade_tune(self):
        with NhlDirs() as d, mock.patch.object(config, "MIN_TUNE_GAMES", 4):
            fx = build()
            res = pipeline.refresh(loader=lambda now, log: fx, now=NOW, log=lambda *_: None)
            self.assertEqual(len(res["cards"]), 8)
            c = res["cards"][0]
            self.assertTrue(c["market_rows"])
            self.assertIsNotNone(c["pick"]["ev"])
            self.assertIn(c["pick"]["tier"], ("pass", "lean", "play", "strong"))
            self.assertTrue(any(" G " in e for e in c["extras"]))
            self.assertTrue(any("back-to-back" in e for card in res["cards"] for e in card["extras"]))
            model = store.load(d.root / "nhl" / "xg_model.json", {})
            coef = dict(zip(model["features"], model["coef"]))
            self.assertLess(coef["dist"] + coef["dist2"], 0)  # farther shots score less
            self.assertGreater(coef["rebound"], 0)
            # finish today's games and grade
            for g in fx["sched"].values():
                g.update(state="OFF", home_score=4, away_score=2)
            res2 = pipeline.refresh(loader=lambda now, log: fx, now=NOW.replace(day=12), log=lambda *_: None)
            self.assertEqual(res2["graded"], 8)
            rec = store.load(d.root / "site_nhl" / "record.json", {})
            self.assertEqual(rec["season_record"]["ats"]["W"] + rec["season_record"]["ats"]["L"], 8)
            self.assertTrue(rec["tiles"])
            entry, _ = pipeline.tune(now=NOW.replace(day=12), log=lambda *_: None, retrain_xg=False)
            self.assertEqual(entry["status"], "updated")


if __name__ == "__main__":
    unittest.main()


class CliTests(unittest.TestCase):
    def test_entry_points_exist(self):
        import importlib
        for mod in ("cfbmodel.__main__", "nflmodel.__main__", "nhlmodel.__main__", "sportscommon.briefings"):
            self.assertTrue(hasattr(importlib.import_module(mod), "main"), mod)


class PartnerOddsTests(unittest.TestCase):
    """Real feed shape (api-web.nhle.com/v1/partner-game/US/now)."""

    def _team(self, ml, tnb, pl, plo, ou, ouo):
        return {"odds": [
            {"description": "MONEY_LINE_3_WAY", "qualifier": "Draw", "value": -205.0},
            {"description": "MONEY_LINE_3_WAY", "qualifier": "", "value": 400.0},
            {"description": "OVER_UNDER", "qualifier": ou, "value": ouo},
            {"description": "MONEY_LINE_2_WAY", "qualifier": "", "value": ml},
            {"description": "PUCK_LINE", "qualifier": pl, "value": plo},
            {"description": "MONEY_LINE_2_WAY_TNB", "qualifier": "", "value": tnb},
        ]}

    def test_parses_two_way_line_and_drops_started_games(self):
        from datetime import datetime, timezone
        from nhlmodel.pipeline import parse_partner_odds
        doc = {"bettingPartner": {"name": "DraftKings"}, "currentOddsDate": "2026-10-12", "games": [
            {"gameId": 1, "startTimeUTC": "2026-10-12T23:00:00Z",
             "awayTeam": self._team(114, 120, "+1.5", -250, "U6.5", -110),
             "homeTeam": self._team(-145, -154, "-1.5", 200, "O6.5", -110)},
            {"gameId": 2, "startTimeUTC": "2026-10-11T23:00:00Z",
             "awayTeam": self._team(3500, 3500, "+4.5", -1150, "U8.5", -750),
             "homeTeam": self._team(-80000, -80000, "-4.5", 650, "O8.5", 470)},
        ]}
        out = parse_partner_odds(doc, datetime(2026, 10, 12, 15, tzinfo=timezone.utc))
        self.assertEqual(list(out), [1])
        r = out[1]
        self.assertEqual((r["ml_home"], r["ml_away"]), (-145, 114))
        self.assertEqual((r["pl_home"], r["pl_away"], r["total"]), (-1.5, 1.5, 6.5))
        self.assertEqual((r["over_odds"], r["under_odds"]), (-110, -110))


class ProbableGoalieTests(unittest.TestCase):
    def test_parse_and_match(self):
        from datetime import date
        from nhlmodel import probables
        doc = {"events": [{"date": "2026-10-12T23:00Z", "competitions": [{"date": "2026-10-12T23:00Z", "competitors": [
            {"team": {"displayName": "Washington Capitals"}, "probables": [
                {"name": "probableStartingGoalie", "athlete": {"fullName": "Logan Thompson"}}]},
            {"team": {"displayName": "Seattle Kraken"}, "probables": [
                {"name": "probableStartingGoalie", "athlete": {"fullName": "Joey Daccord"}}]},
        ]}]}]}
        names = {"Washington Capitals": "WSH", "Seattle Kraken": "SEA"}
        got = probables.fetch_probables([date(2026, 10, 12)], names, getter=lambda u: doc)
        self.assertEqual(got[("2026-10-12", "WSH")], "Logan Thompson")
        starts = {"WSH": [("2026-10-10", 1), ("2026-10-11", 2)]}
        gnames = {1: "L. Thompson", 2: "C. Lindgren", 3: "J. Daccord"}
        self.assertEqual(probables.match("Logan Thompson", "WSH", starts, gnames), 1)
        self.assertEqual(probables.match("Joey Daccord", "SEA", starts, gnames), 3)
        self.assertIsNone(probables.match("New Guy", "SEA", starts, gnames))


class MoneyPuckTests(unittest.TestCase):
    TEAMS = ("team,season,name,team,position,situation,games_played,xGoalsPercentage,xGoalsFor,xGoalsAgainst,"
             "scoreVenueAdjustedxGoalsFor,scoreVenueAdjustedxGoalsAgainst,flurryScoreVenueAdjustedxGoalsFor,flurryScoreVenueAdjustedxGoalsAgainst\n"
             "N.J,2026,N.J,N.J,Team Level,all,10,0.55,33,27,32,28,31.5,28.5\n"
             "N.J,2026,N.J,N.J,Team Level,5on5,10,0.55,20,18,20,18,20,18\n"
             "CAR,2026,CAR,CAR,Team Level,all,10,0.45,27,33,28,32,28.5,31.5\n")
    GOALIES = ("playerId,season,name,team,position,situation,games_played,icetime,xGoals,goals,unblocked_shot_attempts\n"
               "8479979,2026,Jake Oettinger,DAL,G,all,5,18000,14.0,9.0,180\n"
               "8479979,2026,Jake Oettinger,DAL,G,5on5,5,15000,9.0,6.0,140\n")

    def test_parse_and_strength(self):
        from nhlmodel import moneypuck
        t = moneypuck.parse_teams(self.TEAMS)
        self.assertEqual(set(t), {"NJD", "CAR"})
        self.assertEqual(t["NJD"]["xgf"], 31.5)
        mp = {"cur": {"teams": t, "goalies": {}}, "prev": {"teams": {}, "goalies": {}}}
        s = moneypuck.team_strength(mp, prior_games=0)
        self.assertAlmostEqual(sum(s["NJD"]) - sum(s["CAR"]), 0.6, places=6)  # (3.15-2.85) - (2.85-3.15)

    def test_goalie_rates(self):
        from nhlmodel import moneypuck
        g = moneypuck.parse_goalies(self.GOALIES)
        self.assertEqual(g[8479979]["shots"], 180)
        r = moneypuck.goalie_rates({"cur": {"teams": {}, "goalies": g}, "prev": {"teams": {}, "goalies": {}}}, n0=20)
        self.assertAlmostEqual(r[8479979]["rate"], 5.0 / 200)
