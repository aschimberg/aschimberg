import unittest
from datetime import datetime, timezone
from unittest import mock

from cfbmodel import store
from nflmodel import config, margins, pipeline
from tests import test_pipeline as tp
from tests.fake_nfl import build


class NflDirs(tp.TempDirs):
    def __enter__(self):
        super().__enter__()
        root = self.root
        self.p2 = [
            mock.patch.object(config, "DATA_DIR", root / "nfl"),
            mock.patch.object(config, "CACHE_DIR", root / "nfl" / "cache"),
            mock.patch.object(config, "SITE_DATA_DIR", root / "site_nfl"),
            mock.patch.object(pipeline, "PICKS_PATH", root / "nfl" / "picks.json"),
            mock.patch.object(pipeline, "LINE_HISTORY_PATH", root / "nfl" / "lh.json"),
            mock.patch.dict(config.WEIGHTS, {"epa": 0.6, "elo": 0.4}),
            mock.patch.object(config, "HOME_FIELD", 1.6),
        ]
        for p in self.p2:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.p2:
            p.stop()
        super().__exit__(*exc)


NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)  # between week 5 and week 6 kickoffs


class NflTests(unittest.TestCase):
    def test_parse_and_spread_convention(self):
        games, _ = build()
        g = next(x for x in games if x["season"] == 2026 and x["week"] == 1)
        self.assertEqual(g["spread"] is None, False)
        # nflverse spread_line +3 (home favored) -> our home spread -3
        self.assertTrue(g["start"].endswith("Z"))

    def test_key_numbers_weight_three_and_seven(self):
        games, _ = build()
        real = [dict(g) for g in games]
        # inject realistic key-number mass: many 3- and 7-point games
        for i, g in enumerate(real[:3000]):
            if g["completed"]:
                g["home_pts"], g["away_pts"] = (20, 17) if i % 4 == 0 else (24, 17) if i % 7 == 0 else (g["home_pts"], g["away_pts"])
        w = margins.key_weights(real)
        self.assertGreater(w[3], w[4])
        self.assertGreater(w[7], w[8])
        d = margins.pmf(-2.0, w)
        hc, push, ac = margins.cover_probs(d, 3.0)  # home +3, projected to lose by 2
        self.assertGreater(push, 0.03)
        self.assertAlmostEqual(hc + push + ac, 1.0, places=6)

    def test_slate_adds_next_week_when_only_monday_left(self):
        games, _ = build()
        cur = [g for g in games if g["season"] == 2026]
        for g in cur:
            if g["week"] == 6:
                g["completed"] = True
        mnf = next(g for g in cur if g["week"] == 6)
        mnf["completed"] = False
        _, slate = pipeline.current_slate(cur)
        self.assertEqual({g["week"] for g in slate}, {6, 7})
        self.assertEqual(pipeline.slate_label(slate), "Weeks 6–7")

    def test_refresh_end_to_end_with_qb_adjustment(self):
        with NflDirs() as d:
            games, loader = build()
            res = pipeline.refresh(games=games, pbp_loader=loader, now=NOW, log=lambda *_: None)
            self.assertEqual(len(res["cards"]), 16)
            meta = store.load(d.root / "site_nfl" / "meta.json", {})
            self.assertEqual(meta["slate_label"], "Week 6")
            kc = next(c for c in res["cards"] if "KC" in (c["home"]["abbr"], c["away"]["abbr"]))
            self.assertTrue(any("Backup QB (-" in e for e in kc["extras"]), kc["extras"])
            card = res["cards"][0]
            self.assertIn(card["pick"]["tier"], ("pass", "lean", "play", "strong"))
            self.assertIsNotNone(card["pick"]["cover_prob"])
            self.assertIn("components", card["confidence"])
            ratings = store.load(d.root / "site_nfl" / "ratings.json", {})["teams"]
            self.assertEqual(len(ratings), 32)
            picks = store.load(d.root / "nfl" / "picks.json", {})["picks"]
            self.assertEqual(len(picks), 16)

    def test_grade_and_tune(self):
        with NflDirs() as d, mock.patch.object(config, "MIN_TUNE_GAMES", 5):
            games, loader = build()
            pipeline.refresh(games=games, pbp_loader=loader, now=NOW, log=lambda *_: None)
            for g in games:
                if g["season"] == 2026 and g["week"] == 6:
                    g.update(completed=True, home_pts=24.0, away_pts=17.0)
            later = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
            res = pipeline.refresh(games=games, pbp_loader=loader, now=later, log=lambda *_: None)
            self.assertEqual(res["graded"], 16)
            rec = store.load(d.root / "site_nfl" / "record.json", {})
            self.assertEqual(rec["season_record"]["su"]["W"] + rec["season_record"]["su"]["L"], 16)
            entry, _ = pipeline.tune(now=later, log=lambda *_: None)
            self.assertEqual(entry["status"], "updated")
            self.assertAlmostEqual(sum(entry["after"]["weights"].values()), 1.0, places=2)


if __name__ == "__main__":
    unittest.main()


class MarginPmfTests(unittest.TestCase):
    def test_key_weighting_keeps_mean_and_side(self):
        from nflmodel import margins
        kw = {k: 1.0 for k in range(61)}
        kw.update({0: 0.05, 3: 3.0, 7: 2.0, 10: 1.4})
        for mu in (-6.5, 0, 3, 10):
            d = margins.pmf(mu, kw)
            self.assertAlmostEqual(sum(m * v for m, v in d.items()), mu, places=3)
        hc, _, ac = margins.cover_probs(margins.pmf(10, kw), -8.5)
        self.assertGreater(hc, ac)
