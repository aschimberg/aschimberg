import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from cfbmodel import ats, briefing, config, model, pipeline, splits, store
from cfbmodel.normalize import select_line
from cfbmodel.ratings import blend
from tests.fake_cfbd import FakeCFBD


class TempDirs:
    """Point every data path at a temp dir."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(config, "DATA_DIR", root / "data"),
            mock.patch.object(config, "CACHE_DIR", root / "data" / "cache"),
            mock.patch.object(config, "SITE_DATA_DIR", root / "site"),
            mock.patch.object(pipeline, "PICKS_PATH", root / "data" / "picks.json"),
            mock.patch.object(pipeline, "LINE_HISTORY_PATH", root / "data" / "line_history.json"),
        ]
        for p in self.patches:
            p.start()
        self.root = root
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()


class ModelTests(unittest.TestCase):
    def test_pick_and_grade(self):
        game = {"home": "A", "away": "B", "neutral": False}
        ratings = {"A": {"rating": 10.0}, "B": {"rating": 0.0}}
        proj = model.project(game, ratings)
        self.assertAlmostEqual(proj["margin"], 10 + config.HOME_FIELD)
        # Market has A -7: model says A by 12.5 -> take A, edge 5.5
        pick = model.make_pick(game, proj, {"spread": -7.0})
        self.assertEqual(pick["ats_team"], "A")
        self.assertEqual(pick["ats_line"], -7.0)
        self.assertAlmostEqual(pick["edge"], 5.5)
        self.assertEqual(pick["tier"], "play")
        self.assertEqual(model.grade("home", -7.0, 30, 20), "W")
        self.assertEqual(model.grade("home", -7.0, 27, 20), "P")
        self.assertEqual(model.grade("home", -7.0, 24, 20), "L")
        self.assertEqual(model.grade("away", -7.0, 24, 20), "W")

    def test_neutral_site_no_hfa(self):
        proj = model.project({"home": "A", "away": "B", "neutral": True}, {"A": {"rating": 3.0}, "B": {"rating": 3.0}})
        self.assertEqual(proj["margin"], 0)
        self.assertAlmostEqual(proj["home_wp"], 0.5)

    def test_blend_scales_elo_to_points(self):
        teams = [f"T{i}" for i in range(20)]
        cur = {
            "sp": [{"team": t, "rating": i - 10.0} for i, t in enumerate(teams)],
            "fpi": [],
            "elo": [{"team": t, "elo": 1500 + (i - 10) * 30} for i, t in enumerate(teams)],
        }
        ratings, _ = blend(cur, None, set(teams))
        # Elo is perfectly correlated with SP+ here, so the blend equals SP+.
        for i, t in enumerate(teams):
            self.assertAlmostEqual(ratings[t]["rating"], i - 10.0, places=1)
        self.assertEqual(ratings["T19"]["rank"], 1)

    def test_prior_season_fallback_regresses(self):
        prior = {"sp": [{"team": t, "rating": v} for t, v in (("A", 20.0), ("B", -20.0), ("C", 0.0))]}
        cur = {"sp": [{"team": "B", "rating": -5.0}, {"team": "C", "rating": 5.0}]}
        ratings, _ = blend(cur, prior, {"A", "B", "C"})
        self.assertEqual(ratings["A"]["sources"], ["prior"])
        self.assertAlmostEqual(ratings["A"]["rating"], 20 * config.PRIOR_SEASON_REGRESSION, places=1)

    def test_select_line_priority_and_median(self):
        lines = [{"provider": "Bovada", "spread": -3.5}, {"provider": "DraftKings", "spread": "-4", "spreadOpen": -2.5}]
        ln = select_line(lines)
        self.assertEqual((ln["book"], ln["spread"], ln["spread_open"]), ("DraftKings", -4.0, -2.5))
        ln = select_line([{"provider": "X", "spread": -3}, {"provider": "Y", "spread": -4}], providers=[])
        self.assertEqual((ln["book"], ln["spread"]), ("median", -3.5))


class AtsTests(unittest.TestCase):
    def test_situational_records(self):
        games = [
            # A home fav -7, wins by 10 -> A covers
            {"id": 1, "season": 2025, "start": "2025-09-01T18:00:00Z", "home": "A", "away": "B", "neutral": False,
             "conference_game": True, "completed": True, "home_pts": 30, "away_pts": 20, "spread": -7.0},
            # A road dog +3 after 20-day gap (bye), loses by 7 -> fails to cover
            {"id": 2, "season": 2025, "start": "2025-09-21T18:00:00Z", "home": "C", "away": "A", "neutral": False,
             "conference_game": False, "completed": True, "home_pts": 27, "away_pts": 20, "spread": -3.0},
        ]
        t = ats.compute_trends(games)
        self.assertEqual(t["A"]["overall"], [1, 1, 0])
        self.assertEqual(t["A"]["home_fav"], [1, 0, 0])
        self.assertEqual(t["A"]["away_dog"], [0, 1, 0])
        self.assertEqual(t["A"]["off_bye"], [0, 1, 0])
        self.assertEqual(t["A"]["conf"], [1, 0, 0])
        self.assertEqual(t["B"]["dog"], [0, 1, 0])
        self.assertNotIn("off_bye", t["C"])

    def test_relevant_trends_follow_situation(self):
        trends = {"A": {"overall": [10, 5, 0], "fav": [8, 2, 0], "dog": [2, 3, 0], "away": [4, 4, 0],
                        "away_fav": [5, 1, 0], "conf": [6, 3, 0], "last10": [6, 4, 0]}}
        game = {"home": "B", "away": "A", "neutral": False, "conference_game": True}
        rel = {r["key"]: r for r in ats.relevant_trends(trends, "A", game, -3.0, 7)}
        self.assertIn("fav", rel)
        self.assertNotIn("dog", rel)
        self.assertIn("away_fav", rel)
        self.assertTrue(rel["fav"]["notable"])
        self.assertNotIn("off_bye", rel)


class SplitsTests(unittest.TestCase):
    def test_sharp_public_and_rlm_flags(self):
        rec = {"spread": {"home_bets": 72, "home_money": 45}}
        # Home spread moved from -3 to -1.5 (+1.5) = toward away despite 72% on home.
        out = splits.analyze(rec, line_move=1.5)
        types = {(f["type"], f["side"]) for f in out["flags"]}
        self.assertIn(("public", "home"), types)
        self.assertIn(("sharp", "away"), types)  # away: 28% bets, 55% money
        self.assertIn(("rlm", "away"), types)
        self.assertEqual(out["markets"]["spread"]["away"], {"bets": 28.0, "money": 55.0})

    def test_match_by_name_and_flip(self):
        games = [{"id": 1, "home": "Ohio State", "away": "Texas A&M"}]
        m = splits.match(games, [{"home": "Texas A and M", "away": "Ohio St.", "spread": {"home_bets": 40, "home_money": 30}}])
        self.assertEqual(m[1]["spread"]["home_bets"], 60)


class PipelineTests(unittest.TestCase):
    NOW = datetime(2026, 10, 10, 11, 0, tzinfo=timezone.utc)  # Saturday of week 7

    def test_refresh_end_to_end(self):
        with TempDirs() as d:
            client = FakeCFBD()
            res = pipeline.refresh(client, now=self.NOW, log=lambda *_: None)
            self.assertEqual(res["slate"]["week"], 7)
            self.assertTrue(res["cards"])
            meta = store.load(d.root / "site" / "meta.json", {})
            self.assertEqual(meta["trend_seasons"], [2020, 2026])
            slate = store.load(d.root / "site" / "slate.json", {})["games"]
            card = slate[0]
            self.assertIsNotNone(card["pick"]["ats_team"])
            self.assertTrue(card["trends"]["home"])
            self.assertEqual(card["line"]["book"], "DraftKings")
            self.assertEqual(meta["poll"], "AP Top 25")
            ranked = [c for c in slate for side in ("home", "away") if c[side]["poll_rank"]]
            self.assertTrue(ranked)
            self.assertIn("logo", card["home"])
            picks = store.load(d.root / "data" / "picks.json", {})["picks"]
            self.assertEqual(len(picks), len([c for c in slate if not c["locked"]]))
            # History cached so a second refresh makes no history calls.
            self.assertTrue((d.root / "data" / "cache" / "history_2020.json").exists())
            calls_before = client.calls
            pipeline.refresh(client, now=self.NOW, log=lambda *_: None)
            self.assertLess(client.calls - calls_before, 12)

    def test_slate_rolls_forward_once_week_is_final(self):
        from datetime import timedelta
        start = datetime(2026, 10, 4, tzinfo=timezone.utc)
        weeks = [{"week": 6, "season_type": "regular", "start": start, "end": start + timedelta(days=8)},
                 {"week": 7, "season_type": "regular", "start": start + timedelta(days=8), "end": start + timedelta(days=15)}]
        sunday = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)
        games = [{"week": 6, "season_type": "regular", "completed": True},
                 {"week": 7, "season_type": "regular", "completed": False}]
        self.assertEqual(pipeline.current_slate(weeks, sunday, games)["week"], 7)
        games[0]["completed"] = False  # a week-6 game still pending -> stay
        self.assertEqual(pipeline.current_slate(weeks, sunday, games)["week"], 6)
        self.assertEqual(pipeline.current_slate(weeks, sunday)["week"], 6)

    def test_picks_lock_at_kickoff_and_grade(self):
        with TempDirs() as d:
            client = FakeCFBD()
            pipeline.refresh(client, now=self.NOW, log=lambda *_: None)
            picks = store.load(d.root / "data" / "picks.json", {})["picks"]
            gid, p = next(iter(picks.items()))
            # Simulate the game finishing, with the line moving after kickoff.
            for g in client._games[2026]:
                if str(g["id"]) == gid:
                    g["completed"], g["homePoints"], g["awayPoints"] = True, 31, 10
            for l in client._lines[2026]:
                if str(l["id"]) == gid:
                    l["lines"][0]["spread"] = -30.0
            later = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
            pipeline.refresh(client, now=later, log=lambda *_: None)
            p2 = store.load(d.root / "data" / "picks.json", {})["picks"][gid]
            self.assertEqual(p2["line"], p["line"])  # locked at pre-kickoff line
            self.assertEqual(p2["result"]["su"], "W" if p["su_team"] == p["home"] else "L")
            self.assertIn(p2["result"]["ats"], ("W", "L", "P"))
            rec = store.load(d.root / "site" / "record.json", {})
            self.assertEqual(rec["season_record"]["su"]["W"] + rec["season_record"]["su"]["L"], 1)

    def test_briefing_contents_and_chunking(self):
        with TempDirs() as d:
            sp_file = d.root / "splits.json"
            client = FakeCFBD()
            wk7 = [g for g in client._games[2026] if g["week"] == 7]
            sp_file.write_text(json.dumps({"source": "test feed", "games": [
                {"home": wk7[0]["homeTeam"], "away": wk7[0]["awayTeam"], "spread": {"home_bets": 30, "home_money": 52}}
            ]}))
            provider = splits.FileProvider(sp_file)
            pipeline.refresh(client, now=self.NOW, splits_provider=provider, log=lambda *_: None)
            meta = store.load(d.root / "site" / "meta.json", {})
            cards = store.load(d.root / "site" / "slate.json", {})["games"]
            record = store.load(d.root / "site" / "record.json", {})
            tz = ZoneInfo("America/Chicago")
            body = briefing.build_briefing(meta, cards, record, datetime(2026, 10, 10).date(), tz)
            for section in ("Picks", "Line moves", "Key ATS trends", "Bet splits"):
                self.assertIn(section, body)
            self.assertIn("Sharp $ on", body)
            self.assertIsNone(briefing.build_briefing(meta, cards, record, datetime(2026, 10, 12).date(), tz))
            parts = briefing.chunk("\n".join(["x" * 100] * 100), limit=1000)
            self.assertTrue(all(len(p) <= 1000 for p in parts))
            self.assertEqual(sum(p.count("x") for p in parts), 100 * 100)

    def test_briefing_window_and_dedupe(self):
        tz = ZoneInfo("America/Chicago")
        with mock.patch.object(config, "BRIEFING_HOUR", 9):
            at = lambda h: datetime(2026, 10, 10, h, 7, tzinfo=timezone.utc).astimezone(tz)
            self.assertFalse(briefing.gate(at(13), {})[0])  # 8:07 CDT
            self.assertTrue(briefing.gate(at(14), {})[0])  # 9:07 CDT
            self.assertTrue(briefing.gate(at(15), {})[0])  # 10:07 CDT (late cron) still ok
            self.assertFalse(briefing.gate(at(15), {"last_sent": "2026-10-10"})[0])  # already sent
            self.assertFalse(briefing.gate(at(16), {})[0])
            # CST (after DST ends): 15:07 UTC = 9:07 CST
            nov = datetime(2026, 11, 14, 15, 7, tzinfo=timezone.utc).astimezone(tz)
            self.assertEqual(nov.hour, 9)
            self.assertTrue(briefing.gate(nov, {})[0])

    def test_briefing_sends_once(self):
        with TempDirs() as d, mock.patch.object(briefing, "STATE_PATH", d.root / "data" / "briefing_state.json"), \
                mock.patch.object(briefing, "send_ntfy", return_value=True) as send:
            pipeline.refresh(FakeCFBD(), now=self.NOW, log=lambda *_: None)
            now = datetime(2026, 10, 10, 14, 7, tzinfo=timezone.utc)
            self.assertTrue(briefing.check(now=now))
            self.assertIsNotNone(briefing.run_briefing(now=now))
            self.assertIsNone(briefing.run_briefing(now=now.replace(hour=15)))
            self.assertEqual(send.call_count, 1)
            self.assertFalse(briefing.check(now=now.replace(hour=15)))
            # No games on Monday.
            self.assertFalse(briefing.check(now=datetime(2026, 10, 12, 14, 7, tzinfo=timezone.utc)))

if __name__ == "__main__":
    unittest.main()
