import unittest

from nflmodel import angles


def g(gid, season, start, home, away, hp=None, ap=None, spread=None, neutral=False):
    return {"id": gid, "season": season, "start": start, "home": home, "away": away, "home_pts": hp, "away_pts": ap,
            "completed": hp is not None, "spread": spread, "neutral": neutral}


class AngleTests(unittest.TestCase):
    def test_hits_and_records(self):
        games = [
            g("1", 2026, "2026-09-10", "ATL", "TB", 0, 10, -1.0),     # ATL shut out (not a blowout)
            g("2", 2026, "2026-09-10", "MIA", "BUF", 3, 31, 3.0),     # MIA blown out by 28
            g("3", 2026, "2026-09-17", "CIN", "ATL", 20, 17, -3.0),   # ATL +3 off shutout, loses by 3: push
            g("4", 2026, "2026-09-17", "MIA", "DET", 24, 27, 7.5),    # MIA home dog 7.5 off blowout, covers
        ]
        last = angles._last_games(games)
        self.assertEqual(angles.hits(games[2], -3.0, last), [("dog_off_shutout", "away")])
        self.assertEqual(sorted(angles.hits(games[3], 7.5, last)), [("dog_off_blowout", "home"), ("home_dog_7", "home")])
        rec = angles.records(games)
        self.assertEqual((rec["dog_off_shutout"]["W"], rec["dog_off_shutout"]["P"]), (0, 1))
        self.assertEqual(rec["dog_off_blowout"]["W"], 1)
        self.assertEqual(rec["home_dog_7"]["W"], 1)
        self.assertIn("ATS", angles.text("home_dog_7", rec))


if __name__ == "__main__":
    unittest.main()
