import unittest
from datetime import date, datetime, timezone
from unittest import mock

from cfbmodel import store
from nbamodel import backtest, config, data, espn, model, pipeline, players
from tests import test_pipeline as tp
from tests.fake_nba import FakeESPN


class NbaDirs(tp.TempDirs):
    def __enter__(self):
        super().__enter__()
        root = self.root
        self.p2 = [
            mock.patch.object(config, "DATA_DIR", root / "nba"),
            mock.patch.object(config, "CACHE_DIR", root / "nba" / "cache"),
            mock.patch.object(config, "SITE_DATA_DIR", root / "site_nba"),
            mock.patch.object(config, "REQUEST_PAUSE", 0.0),
            mock.patch.object(pipeline, "PICKS_PATH", root / "nba" / "picks.json"),
            mock.patch.object(pipeline, "LINE_HISTORY_PATH", root / "nba" / "lh.json"),
            mock.patch.dict(config.WEIGHTS, dict(config.DEFAULT_WEIGHTS)),
            mock.patch.object(config, "HOME_COURT", config.DEFAULT_HOME_COURT),
        ]
        for p in self.p2:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.p2:
            p.stop()
        super().__exit__(*exc)


TODAY = date(2026, 10, 30)
NOW = datetime(2026, 10, 30, 15, tzinfo=timezone.utc)  # 11 AM ET


def loader(fake):
    def _load(now, log):
        today = now.astimezone(espn.ET).date()
        season = config.season_for(today)
        cur = data.update_season(season, today, log=log, fetcher=fake, pause=0)
        prev = data.update_season(season - 1, today, log=log, fetcher=fake, pause=0)
        report = espn.parse_injuries(espn.injuries(fake), data.name_map({**prev["teams"], **cur["teams"]}))
        return {"season": season, "cur": cur, "prev": prev, "injuries": report}
    return _load


class NbaParseTests(unittest.TestCase):
    def test_odds_parsing_home_spread(self):
        # shapes from the live ESPN probe: away favorite (BOS -1.5 at DET) -> home spread +1.5
        o = {"provider": {"displayName": "Draft Kings"}, "details": "BOS -1.5", "overUnder": 218.5, "spread": 1.5,
             "moneyline": {"home": {"close": {"odds": "+102"}, "open": {"odds": "-125"}}, "away": {"close": {"odds": "-122"}}},
             "pointSpread": {"home": {"close": {"line": "+1.5", "odds": "-110"}, "open": {"line": "-1.5"}}}}
        p = espn.parse_odds(o, "DET")
        self.assertEqual((p["spread"], p["total"], p["ml_home"], p["ml_away"], p["spread_open"]), (1.5, 218.5, 102.0, -122.0, -1.5))
        # pickcenter (closing) shape
        pc = {"provider": {"name": "Draft Kings"}, "details": "ORL -5.5", "overUnder": 227.5, "spread": -5.5,
              "overOdds": -108.0, "underOdds": -112.0, "homeTeamOdds": {"moneyLine": -230, "spreadOdds": -108.0},
              "awayTeamOdds": {"moneyLine": 190, "spreadOdds": -112.0}}
        q = espn.parse_odds(pc, "ORL")
        self.assertEqual((q["spread"], q["over_odds"], q["ml_home"], q["away_spread_odds"]), (-5.5, -108.0, -230.0, -112.0))
        self.assertEqual(espn.num("EVEN"), 100.0)
        self.assertEqual(espn.num("o218.5"), 218.5)

    def test_summary_box(self):
        fake = FakeESPN(today=TODAY)
        g = next(x for x in fake.games.values() if x["done"])
        s = espn.parse_summary(fake._summary(g), g["home"], g["away"])
        self.assertEqual(s["close"]["spread"], g["spread"])
        self.assertAlmostEqual(s["team"][g["home"]]["poss"], 88 - 10 + 13 + 0.44 * 22, places=1)
        self.assertEqual(len(s["players"]), 18)
        star = next(p for p in s["players"] if p[0].endswith("0") and p[2] == g["home"])
        bench = next(p for p in s["players"] if p[3] == 10 and p[2] == g["home"])
        self.assertGreater(star[4], bench[4])

    def test_player_adjustment_only_counts_change_from_recent(self):
        vals = {"1": {"name": "Star", "team": "BOS", "value": 6.0, "exp_min": 35}}
        hist_played = {"BOS": [("d", {"1"})] * 10}
        hist_out = {"BOS": [("d", set())] * 10}
        adj, notes = players.adjustment("BOS", vals, hist_played, {"1": 0.0})
        self.assertAlmostEqual(adj, -6.0)
        self.assertEqual(notes[0][0], "Star")
        adj2, _ = players.adjustment("BOS", vals, hist_out, {"1": 0.0})  # long-term absence: already priced in
        self.assertEqual(adj2, 0.0)
        adj3, _ = players.adjustment("BOS", vals, hist_played, {"1": 1 - players.miss_prob("Questionable")})
        self.assertAlmostEqual(adj3, -3.0)


class NbaPipelineTests(unittest.TestCase):
    def test_refresh_grades_tunes_and_writes_site(self):
        injured = {"LAL": [("1170", "LAL Player0", "Out")]}  # LAL's star
        with NbaDirs():
            fake = FakeESPN(today=TODAY, injured=injured)
            out = pipeline.refresh(loader=loader(fake), now=NOW, log=lambda *a: None)
            cards = out["cards"]
            self.assertGreater(len(cards), 0)
            picks = store.load(pipeline.PICKS_PATH, None)["picks"]
            self.assertTrue(all(p["season"] == 2027 for p in picks.values()))
            c = next(c for c in cards if c["line"])
            self.assertIsNotNone(c["pick"]["ats_side"])
            self.assertIn(c["pick"]["tier"], ("strong", "play", "lean", "pass"))
            self.assertIsNotNone(c["pick"]["total_pick"])
            # LAL's star listed out moves LAL's line and shows on the card
            lal = [c for c in cards if "LAL" in (c["home"]["abbr"], c["away"]["abbr"])]
            if lal:
                self.assertTrue(any("LAL injuries" in e for e in lal[0]["extras"]))
            meta = store.load(config.SITE_DATA_DIR / "meta.json", None)
            self.assertEqual(meta["sport"], "nba")
            self.assertEqual(meta["terms"]["start"], "tip-off")
            ratings_doc = store.load(config.SITE_DATA_DIR / "ratings.json", None)
            self.assertEqual(len(ratings_doc["teams"]), 30)

            # a day later the earlier games are final: graded with CLV against the close
            fake2 = FakeESPN(today=date(2026, 11, 2), injured=injured)
            pipeline.refresh(loader=loader(fake2), now=datetime(2026, 11, 2, 15, tzinfo=timezone.utc), log=lambda *a: None)
            picks = store.load(pipeline.PICKS_PATH, None)["picks"]
            graded = [p for p in picks.values() if p.get("result")]
            self.assertGreater(len(graded), 0)
            g0 = graded[0]
            self.assertIn(g0["result"]["ats"], ("W", "L", "P"))
            self.assertIn("clv", g0)
            rec = store.load(config.SITE_DATA_DIR / "record.json", None)
            self.assertTrue(rec["season_record"]["by_week"][0]["label"].startswith("Week of "))

            entry, _ = pipeline.tune(now=datetime(2026, 11, 2, 16, tzinfo=timezone.utc), log=lambda *a: None)
            self.assertIn(entry["status"], ("insufficient", "updated"))

    def test_finished_dates_are_not_refetched(self):
        with NbaDirs():
            fake = FakeESPN(today=TODAY)
            data.update_season(2026, TODAY, log=lambda *a: None, fetcher=fake, pause=0)
            n1 = len(fake.calls)
            data.update_season(2026, TODAY, log=lambda *a: None, fetcher=fake, pause=0)
            self.assertEqual(len(fake.calls), n1)  # past season: every date frozen, every box cached


class NbaBacktestTests(unittest.TestCase):
    def test_walk_forward_has_no_look_ahead(self):
        with NbaDirs():
            fake = FakeESPN(today=TODAY)
            res = backtest.run([2027], now=NOW, log=lambda *a: None, fetcher=fake,
                               start_weights=config.DEFAULT_WEIGHTS, start_hfa=config.DEFAULT_HOME_COURT)
            self.assertGreater(len(res["picks"]), 0)
            # the first day's picks are built from last season only: identical to a run whose data stops before that day
            first_day = min(p["date"] for p in res["picks"].values())
            p0 = next(p for p in res["picks"].values() if p["date"] == first_day)
            self.assertIn(p0["result"]["ats"], ("W", "L", "P"))
            self.assertTrue(res["weeks"][0]["label"].startswith("Week of "))
            backtest.save(res)
            doc = store.load(backtest.site_backtest_path(), None)
            self.assertEqual(doc["sport"], "nba")
            backtest.apply(res, now=NOW)
            w = store.load(pipeline.weights_path(), None)
            self.assertEqual(w["anchor"]["season"], 2027)

    def test_state_before_a_day_ignores_that_day(self):
        with NbaDirs():
            fake = FakeESPN(today=TODAY)
            cur = data.update_season(2027, TODAY, log=lambda *a: None, fetcher=fake, pause=0)
            prev = data.update_season(2026, TODAY, log=lambda *a: None, fetcher=fake, pause=0)
            games = [g for g in cur["games"].values() if g["completed"]]
            day = sorted({g["date"] for g in games})[3]
            prior = model.priors(list(prev["games"].values()), date(2026, 6, 30))
            before = [g for g in games if g["date"] < day]
            st1 = model.build_state(before, list(prev["games"].values()), date.fromisoformat(day), prior)
            # changing the scores of that day's games must not change the state
            for g in games:
                if g["date"] >= day:
                    g["home_score"] += 30
            st2 = model.build_state([g for g in games if g["date"] < day], list(prev["games"].values()), date.fromisoformat(day), prior)
            self.assertEqual(st1["eff"]["off"], st2["eff"]["off"])


if __name__ == "__main__":
    unittest.main()
