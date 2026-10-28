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
