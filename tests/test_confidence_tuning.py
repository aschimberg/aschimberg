import random
import unittest
from unittest import mock

from cfbmodel import confidence, config, pipeline, store, tuning
from tests.fake_cfbd import FakeCFBD
from tests import test_pipeline as tp


def _pick(side, edge):
    return {"ats_side": side, "edge": edge}


class ConfidenceTests(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.multiple(config, HOME_FIELD=2.5)
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_small_edge_can_be_high_when_everything_agrees(self):
        # Line home -3; all three sources have home by ~5-6 -> all cover, tight spread.
        diffs = {"sp": 3.0, "fpi": 3.5, "elo": 2.8}
        trends = {"home": [{"notable": True, "pct": 0.68}], "away": [{"notable": True, "pct": 0.35}]}
        flags = {"flags": [{"type": "sharp", "market": "spread", "side": "home"}]}
        c = confidence.score(_pick("home", 2.6), -3.0, diffs, 1, -1.5, trends, flags, 1.0)
        self.assertEqual(c["agree"], 3)
        self.assertEqual(c["label"], "High")

    def test_disagreeing_sources_lower_confidence_at_same_edge(self):
        agree = confidence.score(_pick("home", 3.0), -3.0, {"sp": 3.5, "fpi": 3.5, "elo": 3.5}, 1, 0, {}, None, 1.0)
        split = confidence.score(_pick("home", 3.0), -3.0, {"sp": 12.0, "fpi": -2.0, "elo": -1.5}, 1, 0, {}, None, 1.0)
        self.assertGreater(agree["score"], split["score"])
        self.assertEqual(split["agree"], 1)

    def test_market_moving_against_pick_lowers_score(self):
        base = dict(diffs={"sp": 4.0, "fpi": 4.0, "elo": 4.0}, hfa_flag=1, trends={}, split_info=None, data_quality=1.0)
        toward = confidence.score(_pick("home", 3.5), -3.0, line_move=-2.0, **base)
        against = confidence.score(_pick("home", 3.5), -3.0, line_move=+2.0, **base)
        self.assertGreater(toward["score"], against["score"])

    def test_no_line_no_confidence(self):
        self.assertIsNone(confidence.score({"ats_side": None}, None, {}, 1, None, {}, None, 1.0))


def _synthetic_picks(n, seed=1, true_w=(1.0, 0.0, 0.0), true_hfa=3.5):
    rng = random.Random(seed)
    picks = {}
    for i in range(n):
        d = {"sp": rng.gauss(0, 12), "fpi": 0.0, "elo": 0.0}
        d["fpi"] = d["sp"] + rng.gauss(0, 8)
        d["elo"] = d["sp"] + rng.gauss(0, 8)
        flag = 0 if i % 10 == 0 else 1
        margin = sum(w * d[k] for w, k in zip(true_w, ("sp", "fpi", "elo"))) + true_hfa * flag + rng.gauss(0, 6)
        picks[str(i)] = {"season": 2026, "diffs": d, "hfa_flag": flag, "line": -round(d["sp"]),
                         "result": {"home_pts": 30 + margin / 2, "away_pts": 30 - margin / 2}}
    return picks


class TuningTests(unittest.TestCase):
    def test_moves_toward_best_source_with_shrinkage(self):
        with tp.TempDirs(), mock.patch.dict(config.WEIGHTS, {"sp": 0.4, "fpi": 0.35, "elo": 0.25}), \
                mock.patch.multiple(config, HOME_FIELD=2.5, TUNE_PRIOR_GAMES=250, MIN_TUNE_GAMES=40):
            e = tuning.tune(_synthetic_picks(400), 2026, log=lambda *_: None)
            self.assertEqual(e["status"], "updated")
            self.assertGreater(e["fit"]["weights"]["sp"], 0.8)
            self.assertGreater(e["after"]["weights"]["sp"], 0.4)  # moved toward SP+
            self.assertLess(e["after"]["weights"]["sp"], e["fit"]["weights"]["sp"])  # but shrunk
            self.assertAlmostEqual(sum(e["after"]["weights"].values()), 1.0, places=2)
            self.assertGreater(e["after"]["home_field"], 2.5)
            self.assertLessEqual(e["rmse_after"], e["rmse_before"])
            # apply() loads the tuned values for the next refresh.
            tuning.apply()
            self.assertEqual(config.WEIGHTS["sp"], e["after"]["weights"]["sp"])
            self.assertEqual(config.HOME_FIELD, e["after"]["home_field"])

    def test_waits_for_minimum_sample(self):
        with tp.TempDirs(), mock.patch.dict(config.WEIGHTS, {"sp": 0.4, "fpi": 0.35, "elo": 0.25}), \
                mock.patch.multiple(config, HOME_FIELD=2.5, MIN_TUNE_GAMES=40):
            e = tuning.tune(_synthetic_picks(20), 2026, log=lambda *_: None)
            self.assertEqual(e["status"], "insufficient")
            self.assertEqual(e["after"]["weights"], {"sp": 0.4, "fpi": 0.35, "elo": 0.25})


class PipelineConfidenceTests(unittest.TestCase):
    def test_refresh_logs_confidence_and_source_margins_then_grade_tunes(self):
        with tp.TempDirs() as d, mock.patch.dict(config.WEIGHTS, {"sp": 0.4, "fpi": 0.35, "elo": 0.25}), \
                mock.patch.multiple(config, HOME_FIELD=2.5, MIN_TUNE_GAMES=3):
            client = FakeCFBD()
            pipeline.refresh(client, now=tp.PipelineTests.NOW, log=lambda *_: None)
            picks = store.load(d.root / "data" / "picks.json", {})["picks"]
            p = next(iter(picks.values()))
            self.assertEqual(set(p["diffs"]), {"sp", "fpi", "elo"})
            self.assertIsInstance(p["confidence"], int)
            self.assertIn(p["conf_label"], ("High", "Medium", "Low"))
            card = store.load(d.root / "site" / "slate.json", {})["games"][0]
            self.assertIn("components", card["confidence"])
            # Finish week 7 and grade; tuning should run on the graded picks.
            for g in client._games[2026]:
                if g["week"] == 7:
                    g["completed"], g["homePoints"], g["awayPoints"] = True, 28, 21
            graded, rec, tuned = pipeline.grade_only(client, now=tp.PipelineTests.NOW, log=lambda *_: None)
            self.assertGreater(graded, 0)
            self.assertEqual(tuned["status"], "updated")
            self.assertIn("High", rec["ats_by_conf"])
            meta = store.load(d.root / "site" / "meta.json", {})
            self.assertEqual(meta["tuning"]["games"], tuned["games"])


if __name__ == "__main__":
    unittest.main()
