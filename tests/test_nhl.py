import unittest
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
