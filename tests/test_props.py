import unittest

from sportscommon import props


class PropsTests(unittest.TestCase):
    def test_kelly_prefers_likely_scorers_over_longshots(self):
        players = [{"name": "Star Back", "team": "KC", "lam": 0.65},      # p ~ 48%
                   {"name": "Deep Threat", "team": "KC", "lam": 0.10},    # p ~ 9.5%
                   {"name": "Slot Guy", "team": "KC", "lam": 0.30}]       # p ~ 26%
        market = {props.norm_name("Star Back"): {"price": 120, "book": "FanDuel"},      # EV +5.6%
                  props.norm_name("Deep Threat"): {"price": 1400, "book": "DK"},        # EV +43%, longshot
                  props.norm_name("Slot Guy"): {"price": 300, "book": "BetMGM"}}        # EV +3.7%
        bets = props.best_bets(props.price(players, market), n=3, min_ev=0.03, min_prob=0.15)
        self.assertEqual([b["name"] for b in bets], ["Star Back", "Slot Guy"])  # longshot fails min_prob
        self.assertTrue(all(b["bet"] for b in bets))

    def test_no_prices_falls_back_to_probability(self):
        players = [{"name": f"P{i}", "team": "T", "lam": l} for i, l in enumerate((0.2, 0.5, 0.35, 0.1))]
        bets = props.best_bets(props.price(players), n=3)
        self.assertEqual([b["name"] for b in bets], ["P1", "P2", "P0"])
        self.assertFalse(any(b["bet"] for b in bets))
        self.assertEqual(bets[0]["note"], "no market price yet")

    def test_parse_market_best_price_and_grade(self):
        ev = {"bookmakers": [
            {"key": "dk", "title": "DraftKings", "markets": [{"key": "player_anytime_td", "outcomes": [
                {"name": "Yes", "description": "James Cook", "price": 110}, {"name": "No", "description": "James Cook", "price": -140}]}]},
            {"key": "fd", "title": "FanDuel", "markets": [{"key": "player_anytime_td", "outcomes": [
                {"name": "Yes", "description": "James Cook", "price": 125}]}]}]}
        m = props.parse_market(ev, "player_anytime_td")
        self.assertEqual((m["jamescook"]["price"], m["jamescook"]["book"], m["jamescook"]["n_books"]), (125, "FanDuel", 2))
        bets = [{"name": "James Cook", "pid": "00-1", "bet": True, "odds": 125}, {"name": "Dalton Kincaid", "pid": "00-2", "bet": True, "odds": 300}]
        props.grade(bets, ["00-1"], ["J.Cook"])
        self.assertEqual([(b["result"], b["units"]) for b in bets], [("W", 1.25), ("L", -1.0)])
        rec = props.record([{"props": bets}])
        self.assertEqual((rec["W"], rec["L"], rec["units"]), (1, 1, 0.25))

    def test_record_per_game_and_top_pick(self):
        games = [{"props": [{"name": "A", "p": 0.5, "result": "W", "bet": False}, {"name": "B", "p": 0.3, "result": "W", "bet": False},
                            {"name": "C", "p": 0.2, "result": "L", "bet": False}]},
                 {"props": [{"name": "D", "p": 0.4, "result": "L", "bet": False}, {"name": "E", "p": 0.35, "result": "L", "bet": False},
                            {"name": "F", "p": 0.25, "result": "W", "bet": True, "units": 3.0}]},
                 {"props": [{"name": "G", "p": 0.5}]}]  # not graded yet
        rec = props.record(games)
        self.assertEqual((rec["games"], rec["hits_per_game"], rec["exp_per_game"], rec["picks_per_game"]), (2, 1.5, 1.0, 3.0))
        self.assertEqual((rec["all"]["W"], rec["all"]["L"]), (3, 3))
        self.assertEqual((rec["top"]["W"], rec["top"]["L"]), (1, 1))
        self.assertEqual((rec["W"], rec["L"], rec["units"]), (1, 0, 3.0))
        self.assertIsNone(props.record([])["hits_per_game"])


if __name__ == "__main__":
    unittest.main()
