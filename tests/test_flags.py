import unittest

from cfbmodel import flags, normalize


class FlagTests(unittest.TestCase):
    def test_compute(self):
        night = "2026-11-08T00:30:00Z"  # 7:30 PM ET
        day = "2026-11-07T17:00:00Z"    # noon ET
        self.assertEqual(flags.compute(14, 5, -6.0, False, day), ["lower_ranked_home_fav"])
        self.assertEqual(flags.compute(14, 5, -7.5, False, day), [])              # more than 7
        self.assertEqual(flags.compute(5, 14, -6.0, False, day), [])              # home ranked higher
        self.assertEqual(flags.compute(None, 12, -3.0, False, day), ["unranked_home_fav"])
        self.assertEqual(flags.compute(None, 12, 3.0, False, day), [])            # home is the underdog
        self.assertEqual(flags.compute(None, 12, -3.0, True, day), [])            # neutral site
        self.assertEqual(flags.compute(11, 1, -7.0, False, night), ["lower_ranked_home_fav", "ranked_night_1h_under"])
        self.assertEqual(flags.compute(3, 9, 2.5, False, night), ["ranked_night_1h_under"])
        self.assertEqual(flags.compute(3, 9, 2.5, False, night, start_tbd=True), [])

    def test_grade(self):
        r = flags.grade(["lower_ranked_home_fav", "ranked_night_1h_under"], -6.0, 54.5, 30, 20, 24)
        self.assertEqual(r["home_fav_ats"], "W")
        self.assertEqual(r["h1_under"], "W")     # 24 vs 27.5
        self.assertEqual(flags.grade(["unranked_home_fav"], -3.0, 50.0, 20, 17, None)["home_fav_ats"], "P")

    def test_first_half_points(self):
        raw = {"homeLineScores": [7, 10, 0, 3], "awayLineScores": [0, 3, 7, 7]}
        self.assertEqual(normalize._first_half(raw), 20)
        self.assertIsNone(normalize._first_half({}))


if __name__ == "__main__":
    unittest.main()
