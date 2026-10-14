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


class NhlPropsTests(unittest.TestCase):
    def test_lineup_scaled_rates(self):
        from nhlmodel import props as nhlprops
        mp = {"cur": {1: {"name": "Top Sniper", "team": "TOR", "pos": "C", "gp": 20, "toi": 0, "ixg": 9.0, "goals": 12},
                      2: {"name": "Stay Home D", "team": "TOR", "pos": "D", "gp": 20, "toi": 0, "ixg": 1.0, "goals": 1},
                      **{10 + i: {"name": f"Depth F{i}", "team": "OTT", "pos": "L", "gp": 20, "toi": 0, "ixg": 3.0, "goals": 3}
                         for i in range(6)}},
              "prev": {}}
        means = nhlprops.position_means(mp["cur"])
        game = {"home": "TOR", "away": "MTL", "skaters": {"home": [1, 2, 3], "away": []},
                "skater_names": {"1": "Top Sniper", "2": "Stay Home D", "3": "Call Up"}}
        rows = nhlprops.team_players("TOR", game, 3.3, 3.0, mp, means)
        lam = {r["name"]: r["lam"] for r in rows}
        self.assertGreater(lam["Top Sniper"], lam["Call Up"])
        self.assertGreater(lam["Call Up"], 0)  # unknown call-up gets the position prior
        self.assertGreater(lam["Top Sniper"], lam["Stay Home D"])
        base = nhlprops.team_players("TOR", game, 3.0, 3.0, mp, means)
        self.assertAlmostEqual(rows[0]["lam"] / base[0]["lam"], 1.1, places=3)  # scales with projected goals


if __name__ == "__main__":
    unittest.main()
