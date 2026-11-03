import random
import unittest

from cbbmodel import backtest, ratings, shadow


def _season(seed, teams, strength, n_days=60, season=2026):
    rng = random.Random(seed)
    games = []
    for d in range(n_days):
        order = teams[:]
        rng.shuffle(order)
        for i in range(0, len(order) - 1, 2):
            h, a = order[i], order[i + 1]
            poss = 68 + rng.gauss(0, 4)
            hs = round((104 + strength[h] - strength[a] / 2 + 2.2) * poss / 100 + rng.gauss(0, 7))
            as_ = round((104 + strength[a] - strength[h] / 2 - 2.2) * poss / 100 + rng.gauss(0, 7))
            games.append({"id": f"{season}-{d}-{i}", "season": season, "start": f"2025-11-{1 + d % 28:02d}T00:00Z" if d < 28 else f"2025-12-{1 + d % 28:02d}T00:00Z",
                          "h": h, "a": a, "neutral": False, "done": True, "hs": hs, "as": as_, "hp": poss, "ap": poss,
                          "spread": -round((strength[h] - strength[a]) * 1.5 * 0.68 + 3, 1) - 0.5, "total": 141.5})
    return games


class TestCBB(unittest.TestCase):
    def test_ratings_recover_order(self):
        teams = [str(i) for i in range(20)]
        strength = {t: (i - 10) * 1.5 for i, t in enumerate(teams)}
        r = ratings.solve(_season(1, teams, strength), set(teams), prior_games=1)
        net = {t: r["teams"][t][0] - r["teams"][t][1] for t in teams}
        self.assertGreater(net["19"], net["10"])
        self.assertGreater(net["10"], net["0"])
        hp, ap, poss = ratings.predict(r, "19", "0")
        self.assertGreater(hp - ap, 15)
        self.assertAlmostEqual(poss, 68, delta=3)

    def test_backtest_runs_without_lookahead(self):
        teams = [str(i) for i in range(16)]
        strength = {t: (i - 8) * 1.2 for i, t in enumerate(teams)}
        prev = _season(2, teams, strength, season=2025)
        cur = _season(3, teams, strength)
        out = backtest.run(prev, cur, set(teams), first=10)
        self.assertGreater(out["games"], 50)
        self.assertIn("plays", out)
        self.assertIn("slope", out["margin"])

    def test_grade(self):
        p = {"side": "home", "spread": -3.5, "ou": "over", "total": 140.5}
        self.assertEqual(shadow._grade(p, {"hs": 80, "as": 70}), {"side": "W", "total": "W"})
        self.assertEqual(shadow._grade(p, {"hs": 70, "as": 68}), {"side": "L", "total": "L"})


if __name__ == "__main__":
    unittest.main()
