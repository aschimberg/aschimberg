import unittest

from nhlmodel import pipeline as P


class PuckLineTests(unittest.TestCase):
    MK = {"home_p15": 0.82, "away_p15": 0.68, "home_m15": 0.32, "away_m15": 0.18}

    def test_alt_only_for_dogs_with_a_plus_one_and_a_half_price(self):
        o = {"pl_away": 1.5, "pl_away_odds": -180.0, "pl_home": -1.5, "pl_home_odds": 150.0}
        alt = P.puck_line_alt("away", 0.40, 0.03, o, self.MK)
        self.assertEqual((alt["line"], alt["odds"], alt["prob"]), (1.5, -180.0, 0.68))
        self.assertAlmostEqual(alt["ev"], 0.68 * (100 / 180) - 0.32, places=4)
        self.assertEqual(alt["better"], alt["ev"] > 0.03)
        self.assertIsNone(P.puck_line_alt("home", 0.60, 0.03, o, self.MK))  # favorite: no alt
        self.assertIsNone(P.puck_line_alt("away", 0.40, 0.03, {"pl_away": -1.5, "pl_away_odds": 150.0}, self.MK))
        self.assertIsNone(P.puck_line_alt("away", 0.40, 0.03, {}, self.MK))  # no puck line posted

    def test_dog_record_counts_one_goal_losses_and_units(self):
        def pick(side, hs, as_, odds=150.0, pl_odds=-180.0, better=False, mkt=0.4):
            won = (hs > as_) == (side == "home")
            return {"ats_side": side, "mkt_prob": mkt, "odds": odds, "ev": 0.03,
                    "pl_alt": {"odds": pl_odds, "better": better, "ev": 0.02, "prob": 0.66},
                    "result": {"home_pts": hs, "away_pts": as_, "ats": "W" if won else "L",
                               "units": 1.5 if won else -1.0}}
        graded = [pick("away", 3, 4),            # dog wins
                  pick("away", 3, 2),            # loses by one (OT/SO or late) -> covers
                  pick("away", 5, 2),            # loses by three
                  pick("home", 2, 3, better=True),  # dog loses by one, card said +1.5
                  pick("home", 4, 1, mkt=0.6)]   # favorite: not a dog pick
        d = P.dog_record(graded)
        self.assertEqual((d["ml"]["W"], d["ml"]["L"]), (1, 3))
        self.assertEqual((d["pl"]["W"], d["pl"]["L"], d["pl"]["priced"]), (3, 1, 4))
        self.assertAlmostEqual(d["pl"]["units"], round(3 * (100 / 180) - 1, 2), places=2)
        self.assertEqual((d["losses"], d["lost_by_one"]), (3, 2))
        self.assertEqual(d["best_of"]["W"], 2)  # the win, plus the +1.5 the card chose


if __name__ == "__main__":
    unittest.main()
