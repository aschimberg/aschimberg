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
