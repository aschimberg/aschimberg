import unittest
from datetime import date, datetime, timezone
from unittest import mock

from cfbmodel import store
from nbamodel import pipeline
from sportscommon import polymarket as pm
from tests.fake_nba import FakeESPN
from tests.test_nba import NOW, TODAY, NbaDirs, loader

NOW_PM = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


def side(long_, name, abbr, safe, ordering):
    return {"long": long_, "description": safe, "team": {"name": name, "abbreviation": abbr.lower(),
            "displayAbbreviation": abbr, "safeName": safe, "alias": name, "ordering": ordering}}


EVENT = {  # trimmed from a real /v1/events response
    "slug": "nba-lal-pho-2026-11-15", "startTime": "2026-11-15T21:00:00Z",
    "markets": [
        {"slug": "aachc-nba-lead-3pm", "sportsMarketType": "futures", "marketType": "futures", "marketSides": []},
        {"slug": "aec-nba-lal-pho-2026-11-15", "sportsMarketType": "basketball_team_full_game_winner",
         "marketType": "moneyline", "closed": False,
         "marketSides": [side(True, "Los Angeles Lakers", "LAL", "Lakers", "away"),
                         side(False, "Phoenix Suns", "PHO", "Suns", "home")]},
    ],
}
BBO = {"marketData": {"marketSlug": "aec-nba-lal-pho-2026-11-15", "bestBid": {"value": "0.6100"},
                      "bestAsk": {"value": "0.6500"}, "lastTradePx": {"value": "0.6300"},
                      "openInterest": "1364.25", "state": "MARKET_STATE_OPEN"}}


def getter(path, query=None):
    if path == "/v1/events":
        return {"events": [EVENT]}
    if path.endswith("/bbo"):
        return BBO
    raise AssertionError(path)


class PolymarketUnitTests(unittest.TestCase):
    def test_long_side_is_away_and_home_price_is_one_minus_bid(self):
        g = pm.parse_event(EVENT)
        self.assertEqual(g["market"], "aec-nba-lal-pho-2026-11-15")
        self.assertEqual((g["away"]["abbr"], g["home"]["abbr"]), ("LAL", "PHO"))
        q = pm.quote(g, getter=getter)
        self.assertEqual(q["away"], {"ask": 0.65, "bid": 0.61})
        self.assertEqual(q["home"], {"ask": 0.39, "bid": 0.35})
        self.assertAlmostEqual(q["spread"], 0.04)

    def test_matches_team_names_and_start_time(self):
        games = [pm.parse_event(EVENT)]
        self.assertIsNotNone(pm.find(games, "Phoenix Suns", "Los Angeles Lakers", "2026-11-15T21:00:00Z"))
        self.assertIsNone(pm.find(games, "Los Angeles Lakers", "Phoenix Suns", "2026-11-15T21:00:00Z"))  # home/away swapped
        self.assertIsNone(pm.find(games, "Phoenix Suns", "Los Angeles Lakers", "2026-11-17T21:00:00Z"))  # another game
        nets = {"name": "Brooklyn Nets", "alias": "Brooklyn Nets", "safe": "Nets"}
        self.assertFalse(pm._same_team(nets, "Charlotte Hornets"))
        self.assertTrue(pm._same_team(nets, "BKN Nets"))

    def test_ev_includes_taker_fee(self):
        # 0.40 + 0.095 x 0.40 x 0.60 = 0.4228 per contract
        self.assertAlmostEqual(pm.cost(0.40), 0.4228)
        self.assertAlmostEqual(pm.ev(0.45, 0.40), 0.45 / 0.4228 - 1)
        self.assertLess(pm.ev(0.40, 0.40), 0)  # a fair price loses the fee

    def test_value_side_needs_min_ev_and_a_tight_market(self):
        g = pm.parse_event(EVENT)
        q = pm.quote(g, getter=getter)
        names = {"home": "Phoenix Suns", "away": "Los Angeles Lakers"}
        blk = pm.price(q, 0.45, names, g, NOW_PM)  # Suns 45% at 39c (+ fee) -> value
        self.assertEqual(blk["value"]["side"], "home")
        self.assertAlmostEqual(blk["value"]["ev"], round(pm.ev(0.45, 0.39), 4))
        self.assertIsNone(pm.price(q, 0.40, names, g, NOW_PM)["value"])  # too thin an edge after the fee
        self.assertIsNone(pm.price(q, 0.45, names, g, NOW_PM, bet_ok=False)["value"])
        wide = dict(q, spread=0.10)
        self.assertIsNone(pm.price(wide, 0.60, names, g, NOW_PM)["value"])
        self.assertIsNone(pm.price(q, None, names, g, NOW_PM)["value"])  # no line, no model price

    def test_bet_keeps_first_price_and_grades_with_clv(self):
        g = pm.parse_event(EVENT)
        names = {"home": "Phoenix Suns", "away": "Los Angeles Lakers"}
        q1 = pm.quote(g, getter=getter)
        e1 = {}
        pm.carry(e1, None, pm.price(q1, 0.45, names, g, NOW_PM), "t1")
        self.assertEqual(e1["poly_bet"]["price"], 0.39)
        q2 = {"home": {"ask": 0.44, "bid": 0.42}, "away": {"ask": 0.58, "bid": 0.56}, "spread": 0.02, "oi": 1, "last": 0.43}
        e2 = {}
        pm.carry(e2, e1, pm.price(q2, 0.45, names, g, NOW_PM), "t2")  # value gone; the bet stands
        self.assertEqual((e2["poly_bet"]["price"], e2["poly_bet"]["at"]), (0.39, "t1"))
        self.assertEqual(e2["poly"]["home"]["ask"], 0.44)
        pm.grade(e2, 110, 104)
        b = e2["poly_bet"]
        self.assertEqual(b["result"], "W")
        self.assertAlmostEqual(b["profit"], round(1 / pm.cost(0.39) - 1, 4))
        self.assertAlmostEqual(b["clv"], 0.05)  # closed at 44c, bought at 39c
        s = pm.summarize({"g": {**e2, "season": 2027}}, 2027)
        self.assertEqual((s["W"], s["L"], s["open"]), (1, 0, 0))
        self.assertGreater(s["units"], 1.4)

    def test_tie_settles_at_half(self):
        e = {"poly_bet": {"side": "away", "price": 0.5, "cost": pm.cost(0.5), "ev": 0.03}}
        pm.grade(e, 20, 20)
        self.assertEqual(e["poly_bet"]["result"], "P")
        self.assertAlmostEqual(e["poly_bet"]["profit"], 0.5 / pm.cost(0.5) - 1, places=4)

    def test_fetch_quotes_never_raises(self):
        def boom(path, query=None):
            raise OSError("down")
        with mock.patch.dict("os.environ", {"POLYMARKET": "on"}):
            self.assertEqual(pm.fetch_quotes("nba", [("1", "Phoenix Suns", "Los Angeles Lakers", None)], NOW_PM, 48,
                                             log=lambda *a: None, getter=boom), {})
            got = pm.fetch_quotes("nba", [("1", "Phoenix Suns", "Los Angeles Lakers", "2026-11-15T21:00:00Z")], NOW_PM, 96,
                                  log=lambda *a: None, getter=getter)
        self.assertEqual(list(got), ["1"])


def fake_quotes(home_ask):
    def fetch(league, games, now, hours, log=print, getter=None):
        q = {"home": {"ask": home_ask, "bid": round(home_ask - 0.02, 2)},
             "away": {"ask": round(1 - home_ask + 0.02, 2), "bid": round(1 - home_ask, 2)},
             "spread": 0.02, "oi": 500.0, "last": home_ask}
        return {k: ({"market": f"aec-{k}", "slug": f"ev-{k}"}, q) for k, *_ in games}
    return fetch


class PolymarketNbaPipelineTests(unittest.TestCase):
    def test_value_bets_are_logged_shown_and_graded(self):
        with NbaDirs(), mock.patch.object(pipeline.model, "early", lambda *a: False):
            # home teams priced at 20c: every home side the anchored model gives 25%+ is a value bet
            with mock.patch.object(pipeline.polymarket, "fetch_quotes", fake_quotes(0.20)):
                out = pipeline.refresh(loader=loader(FakeESPN(today=TODAY)), now=NOW, log=lambda *a: None)
            priced = [c for c in out["cards"] if c["polymarket"]]
            self.assertTrue(priced)
            c = next(c for c in priced if c["line"])
            self.assertEqual(c["polymarket"]["home"]["ask"], 0.20)
            self.assertIsNotNone(c["polymarket"]["home"]["p"])
            picks = store.load(pipeline.PICKS_PATH, None)["picks"]
            bets = {k: p["poly_bet"] for k, p in picks.items() if p.get("poly_bet")}
            self.assertTrue(bets)
            self.assertTrue(all(b["side"] == "home" and b["price"] == 0.20 for b in bets.values()))

            # days later: graded at the entry price, with CLV against the last quote before tip
            with mock.patch.object(pipeline.polymarket, "fetch_quotes", fake_quotes(0.50)):
                pipeline.refresh(loader=loader(FakeESPN(today=date(2026, 11, 2))),
                                 now=datetime(2026, 11, 2, 15, tzinfo=timezone.utc), log=lambda *a: None)
            picks = store.load(pipeline.PICKS_PATH, None)["picks"]
            graded = [p["poly_bet"] for k, p in picks.items() if k in bets and p.get("result")]
            self.assertTrue(graded)
            for b in graded:
                self.assertIn(b["result"], ("W", "L"))
                self.assertEqual(b["price"], 0.20)
                self.assertAlmostEqual(b["profit"], round(1 / pm.cost(0.20) - 1, 4) if b["result"] == "W" else -1.0)
            rec = store.load(pipeline.config.SITE_DATA_DIR / "record.json", None)
            self.assertEqual(rec["polymarket"]["W"] + rec["polymarket"]["L"], len(graded))


if __name__ == "__main__":
    unittest.main()
