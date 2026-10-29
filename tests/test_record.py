import unittest

from sportscommon import record


def pick(tier, res, week=1):
    return {"season": 2026, "season_type": "regular", "week": week, "edge": 1.0,
            "total_pick": {"tier": tier}, "result": {"su": "W", "ats": "W", "total": res}}


class TotalsRecordTests(unittest.TestCase):
    def test_totals_by_tier_and_week(self):
        picks = {str(i): p for i, p in enumerate([
            pick("pass", "W"), pick("lean", "L"), pick("play", "W"), pick("strong", "W", week=2), pick("strong", "P", week=2)])}
        picks["x"] = {"season": 2026, "season_type": "regular", "week": 2, "result": {"su": "L"}}  # no total pick
        r = record.compute(picks, 2026, 2, 4, 7, total_edges=(2, 3.5, 5))
        ou = r["ou"]
        self.assertEqual((ou["all"]["W"], ou["all"]["L"], ou["all"]["P"]), (3, 1, 1))
        self.assertEqual((ou["lean"]["W"], ou["lean"]["L"]), (2, 1))  # lean, play and strong
        self.assertEqual((ou["play"]["W"], ou["play"]["L"]), (2, 0))
        self.assertEqual((ou["strong"]["W"], ou["strong"]["P"]), (1, 1))
        self.assertEqual(ou["edges"], {"lean": 2, "play": 3.5, "strong": 5})
        self.assertEqual([(w["week"], w["ou"]["W"], w["ou"]["L"]) for w in r["by_week"]], [(1, 2, 1), (2, 1, 0)])


if __name__ == "__main__":
    unittest.main()


class PlaysRecordTests(unittest.TestCase):
    def test_sides_and_totals_at_play_or_strong(self):
        from sportscommon import record as recmod
        items = [
            {"tier": "play", "result": {"ats": "W"}, "total_pick": {"tier": "lean"}},                     # side counts at -110
            {"tier": "lean", "result": {"ats": "W", "total": "L"}, "total_pick": {"tier": "strong", "odds": -120}},
            {"tier": "strong", "odds": 150, "result": {"ats": "L", "units": -1.0}},                       # logged payout wins
            {"tier": "play", "odds": 150, "result": {"ats": "W", "units": 1.5, "total": "P", "total_units": 0.0},
             "total_pick": {"tier": "play"}},
            {"tier": "pass", "result": {"ats": "W"}},
        ]
        r = recmod.plays(items)
        self.assertEqual((r["W"], r["L"], r["P"], r["sides"], r["totals"]), (2, 2, 1, 3, 2))
        self.assertAlmostEqual(r["units"], 0.909 - 1.0 - 1.0 + 1.5 + 0.0, places=2)
        self.assertEqual(recmod.plays_sub(recmod.plays([])), "play or strong picks · none graded yet")
        self.assertEqual((r["side"]["W"], r["side"]["L"], r["total"]["L"], r["total"]["P"]), (2, 1, 1, 1))
        self.assertEqual(recmod.plays_sub(r), f"ATS 2–1 · O/U 0–1–1 · {r['units']:+.1f}u")


class ModelCheckTests(unittest.TestCase):
    def _picks(self, wins, losses, prob, start="2026-11-01T00:00:00Z"):
        mk = lambda r: {"tier": "play", "cover_prob": prob, "start": start, "result": {"ats": r}}
        return [mk("W") for _ in range(wins)] + [mk("L") for _ in range(losses)]

    def test_health_statuses(self):
        from sportscommon import record as recmod
        self.assertEqual(recmod.health(recmod.plays(self._picks(5, 5, 0.55)))["status"], "early")
        bad = recmod.plays(self._picks(45, 55, 0.57))      # 45% when the model said 57%
        self.assertLess(bad["z"], -2)
        self.assertEqual(recmod.health(bad)["status"], "retune")
        meh = recmod.plays(self._picks(27, 25, 0.53))      # 52%: under the 54% target, not significant
        self.assertEqual(recmod.health(meh)["status"], "watch")
        good = recmod.plays(self._picks(30, 24, 0.54))     # 55.6%
        self.assertEqual(recmod.health(good)["status"], "ok")
        self.assertIsNone(recmod.check_line({"model_check": {"health": recmod.health(good)}}))
        self.assertTrue(recmod.check_line({"model_check": {"health": recmod.health(bad)}}).startswith("⚠ Model check: Retune"))

    def test_current_model_split(self):
        from sportscommon import record as recmod
        old = self._picks(40, 60, 0.57, start="2026-10-20T00:00:00Z")
        new = self._picks(3, 1, 0.53)
        season, mc = recmod.plays_block(old + new)
        self.assertEqual((season["W"], mc["plays"]["W"], mc["previous"]["W"]), (43, 3, 40))
        self.assertEqual((mc["health"]["status"], mc["previous_health"]["status"]), ("early", "retune"))
