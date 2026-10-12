import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from sportscommon import oddsapi

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
EVENT = {
    "id": "e1", "commence_time": "2026-10-18T20:25:00Z",
    "home_team": "Kansas City Chiefs", "away_team": "Los Angeles Chargers",
    "bookmakers": [
        {"key": "draftkings", "title": "DraftKings", "markets": [
            {"key": "spreads", "outcomes": [{"name": "Kansas City Chiefs", "price": -110, "point": -8.5},
                                            {"name": "Los Angeles Chargers", "price": -110, "point": 8.5}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": -108, "point": 43.5},
                                           {"name": "Under", "price": -112, "point": 43.5}]}]},
        {"key": "fanduel", "title": "FanDuel", "markets": [
            {"key": "spreads", "outcomes": [{"name": "Kansas City Chiefs", "price": -105, "point": -8.0},
                                            {"name": "Los Angeles Chargers", "price": -115, "point": 8.0}]}]},
        {"key": "betmgm", "title": "BetMGM", "markets": [
            {"key": "spreads", "outcomes": [{"name": "Kansas City Chiefs", "price": -110, "point": -9.0},
                                            {"name": "Los Angeles Chargers", "price": -110, "point": 9.0}]}]},
    ],
}


class OddsApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(oddsapi, "CACHE_DIR", Path(self.tmp.name))
        self.p.start()
        self.calls = []

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def fetcher(self, remaining="420"):
        def f(url):
            self.calls.append(url)
            return json.dumps([EVENT]).encode(), {"X-Requests-Remaining": remaining, "X-Requests-Used": "80", "X-Requests-Last": "2"}
        return f

    def test_no_key_means_no_calls(self):
        with mock.patch.dict(os.environ, {"ODDS_API_KEY": ""}):
            self.assertIsNone(oddsapi.get_odds("nfl", "spreads,totals", now=NOW, fetcher=self.fetcher(), log=lambda *_: None))
        self.assertEqual(self.calls, [])

    def test_fetch_caches_and_tracks_credits(self):
        with mock.patch.dict(os.environ, {"ODDS_API_KEY": "k"}):
            ev = oddsapi.get_odds("nfl", "spreads,totals", now=NOW, fetcher=self.fetcher(), log=lambda *_: None)
            again = oddsapi.get_odds("nfl", "spreads,totals", now=NOW + timedelta(hours=2), max_age_hours=6,
                                     fetcher=self.fetcher(), log=lambda *_: None)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("markets=spreads%2Ctotals", self.calls[0])
        self.assertEqual(ev, again)
        self.assertEqual(oddsapi.usage()["remaining"], 420)

    def test_reserve_stops_calls(self):
        with mock.patch.dict(os.environ, {"ODDS_API_KEY": "k", "ODDS_API_RESERVE": "40"}):
            oddsapi.get_odds("nfl", "spreads,totals", now=NOW, fetcher=self.fetcher(remaining="30"), log=lambda *_: None)
            later = oddsapi.get_odds("nfl", "spreads,totals", now=NOW + timedelta(hours=8), max_age_hours=6,
                                     fetcher=self.fetcher(), log=lambda *_: None)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNotNone(later)  # still serves the <24h-old cache

    def test_consensus_and_best_lines(self):
        ev = oddsapi.find_event([EVENT], "Kansas City Chiefs", "Los Angeles Chargers", "2026-10-18T20:25:00Z")
        sp = oddsapi.spreads(ev)
        self.assertEqual(sp["consensus"], -8.5)
        self.assertEqual(sp["n_books"], 3)
        self.assertEqual(sp["main"]["book"], "DraftKings")
        self.assertEqual((sp["best_home"]["point"], sp["best_home"]["book"]), (-8.0, "FanDuel"))
        self.assertEqual((sp["best_away"]["point"], sp["best_away"]["book"]), (9.0, "BetMGM"))
        self.assertEqual(oddsapi.totals(ev)["consensus"], 43.5)

    def test_name_normalization(self):
        ev = dict(EVENT, home_team="Montreal Canadiens", away_team="St Louis Blues")
        self.assertIs(oddsapi.find_event([ev], "Montréal Canadiens", "St. Louis Blues", "2026-10-18T23:00:00Z"), ev)
        self.assertIsNone(oddsapi.find_event([ev], "Montréal Canadiens", "St. Louis Blues", "2026-10-25T23:00:00Z"))


if __name__ == "__main__":
    unittest.main()
