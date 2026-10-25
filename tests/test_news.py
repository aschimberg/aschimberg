import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from sportscommon import news

NOW = datetime(2026, 10, 25, 15, 30, tzinfo=timezone.utc)

ESPN_DOC = {"articles": [
    {"headline": "Ravens rule out Lamar Jackson (ankle) vs. Bengals; Tyler 'Snoop' Huntley to start",
     "description": "Baltimore will start Huntley.", "published": "2026-10-24T20:41:27Z",
     "links": {"web": {"href": "https://espn.com/a1"}},
     "categories": [{"type": "team", "description": "Baltimore Ravens"}, {"type": "athlete", "description": "Lamar Jackson"}]},
    {"headline": "Week 7 NFL highlights: Best plays", "published": "2026-10-25T15:17:27Z", "links": {}, "categories": []},
]}

ROTOWIRE = """<?xml version="1.0"?><rss><channel>
<item><title>Rico Dowdle: Available to play in Paris</title><link>https://rotowire.com/1</link>
<pubDate>Sun, 25 Oct 2026 5:14:00 AM PDT</pubDate><description>Dowdle (knee) is active for Sunday's game against the Saints. Visit RotoWire.com for more</description></item>
<item><title>Kenny Gainwell: Questionable for Sunday</title><link>https://rotowire.com/2</link>
<pubDate>Sun, 25 Oct 2026 6:00:00 AM PDT</pubDate><description>Gainwell (ankle) is questionable against the Panthers.</description></item>
</channel></rss>"""

CBS = """<?xml version="1.0"?><rss><channel>
<item><title>Chiefs vs. Seahawks odds, predictions: Use Kalshi promo code for $55 bonus</title><link>x</link>
<pubDate>Sun, 25 Oct 2026 15:13:27 +0000</pubDate><description>promo</description></item>
<item><title>Bengals' Joe Burrow cleared, returns from injury to face Ravens</title><link>https://cbs.com/b</link>
<pubDate>Sun, 25 Oct 2026 12:00:00 +0000</pubDate><description>Cincinnati gets its QB back.</description></item>
</channel></rss>"""

GOOGLE = """<?xml version="1.0"?><rss><channel>
<item><title>When will Lamar Jackson return? Latest injury update for Ravens QB - USA Today</title>
<link>https://news.google.com/rss/articles/abc</link><pubDate>Sun, 25 Oct 2026 14:20:00 GMT</pubDate>
<description>&lt;a href="x"&gt;When will...&lt;/a&gt;</description><source url="https://usatoday.com">USA Today</source></item>
<item><title>Ravens rule out Lamar Jackson (ankle) vs. Bengals; Tyler 'Snoop' Huntley to start - ESPN</title>
<link>https://news.google.com/rss/articles/dup</link><pubDate>Sat, 24 Oct 2026 19:48:00 GMT</pubDate><source url="https://espn.com">ESPN</source></item>
<item><title>[!LIVESTREAMs!]Tv] Baltimore Ravens vs Cincinnati Bengals 𝐋𝐈𝐕𝐄 Free Stream - FC東京オフィシャルホームページ</title><link>s</link>
<pubDate>Sun, 25 Oct 2026 14:00:00 GMT</pubDate><source url="s">FC東京オフィシャルホームページ</source></item>
<item><title>Cincinnati Bengals vs Baltimore Ravens Prediction, Pick, Odds -- NFL Week 7 - Action Network</title><link>p</link>
<pubDate>Sun, 25 Oct 2026 14:00:00 GMT</pubDate><source url="p">Action Network</source></item>
<item><title>Will Kenny Gainwell play in Week 7? Injury update for Bucs RB - Heavy</title><link>h</link>
<pubDate>Sun, 25 Oct 2026 14:30:00 GMT</pubDate><source url="h">Heavy</source></item>
<item><title>Old Ravens story - WBAL</title><link>y</link><pubDate>Mon, 19 Oct 2026 10:00:00 GMT</pubDate><source url="w">WBAL</source></item>
</channel></rss>"""

EMPTY = '<?xml version="1.0"?><rss><channel></channel></rss>'

INFO = {"BAL": {"name": "Baltimore Ravens", "terms": ["Ravens"]}, "CIN": {"name": "Cincinnati Bengals", "terms": ["Bengals"]},
        "TB": {"name": "Tampa Bay Buccaneers", "terms": ["Buccaneers"]}}


class FakeFeeds:
    def __init__(self):
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if "espn.com" in url:
            return json.dumps(ESPN_DOC)
        if "rotowire" in url:
            return ROTOWIRE
        if "cbssports" in url:
            return CBS
        if "news.google.com" in url:
            return GOOGLE if "Baltimore" in url else EMPTY
        raise OSError("feed down")  # Yahoo / PFT: failures are skipped


class NewsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "news.json"
        self.env = mock.patch.dict(os.environ, {"NEWS": "on"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_dates(self):
        self.assertEqual(news.parse_date("Sun, 25 Oct 2026 5:14:00 AM PDT"), "2026-10-25T12:14:00Z")
        self.assertEqual(news.parse_date("Sun, 25 Oct 2026 11:06:21 -0400"), "2026-10-25T15:06:21Z")
        self.assertEqual(news.parse_date("2026-10-24T20:41:27Z"), "2026-10-24T20:41:27Z")
        self.assertIsNone(news.parse_date("yesterday"))

    def test_collect_tags_dedupes_filters_and_caches(self):
        feeds = FakeFeeds()
        items = news.collect("nfl", INFO, NOW, self.path, log=lambda *_: None, fetcher=feeds)
        titles = [it["t"] for it in items]
        self.assertFalse(any("promo code" in t for t in titles))          # betting promo dropped
        self.assertFalse(any("highlights" in t for t in titles))          # league round-up dropped
        self.assertFalse(any("Old Ravens" in t for t in titles))          # older than 72h
        self.assertFalse(any("Stream" in t or "Prediction" in t for t in titles))  # stream spam and previews dropped
        self.assertEqual(sum("rule out Lamar Jackson" in t for t in titles), 1)  # ESPN + Google copy -> one
        usa = next(it for it in items if it["src"] == "USA Today")
        self.assertEqual(usa["t"], "When will Lamar Jackson return? Latest injury update for Ravens QB")  # " - USA Today" stripped
        burrow = next(it for it in items if "Burrow" in it["t"])
        self.assertEqual((burrow["teams"], burrow["tone"]), (["BAL", "CIN"], 1))
        ravens = news.for_team(items, "BAL")
        self.assertTrue(ravens and all("t" in r and "u" in r for r in ravens))
        self.assertLess(news.team_tone(items, "BAL"), 0)  # Lamar ruled out
        n = len(feeds.calls)
        news.collect("nfl", INFO, NOW, self.path, log=lambda *_: None, fetcher=feeds)
        self.assertEqual(len(feeds.calls), n)  # inside the cache window: no refetch

    def test_player_flags(self):
        items = news.collect("nfl", INFO, NOW, self.path, log=lambda *_: None, fetcher=FakeFeeds())
        self.assertEqual(news.player_note(items, "Kenny Gainwell")["flag"], "warn")  # RotoWire 'questionable', not Heavy's 'injury update'
        self.assertIn("Questionable", news.player_note(items, "Kenny Gainwell")["t"])
        self.assertEqual(news.player_note(items, "Rico Dowdle")["flag"], "ok")
        self.assertEqual(news.player_note(items, "Lamar Jackson", "BAL")["flag"], "warn")
        self.assertIsNone(news.player_note(items, "Josh Allen"))
        mixed = [{"t": "Eagles RB Saquon Barkley (hamstring) logs full practice, questionable to play", "u": "u", "src": "ESPN",
                  "at": "2026-10-25T12:00:00Z", "teams": ["PHI"], "tone": 0}]
        self.assertEqual(news.player_note(mixed, "Saquon Barkley")["flag"], "warn")  # official status beats "full practice"
        gainwell = next(it for it in items if it["t"].startswith("Kenny Gainwell"))
        self.assertEqual(gainwell["teams"], [])  # RotoWire summary's opponent doesn't tag a team

    def test_off_switch_and_line_note(self):
        with mock.patch.dict(os.environ, {"NEWS": "off"}):
            self.assertEqual(news.collect("nfl", INFO, NOW, self.path, fetcher=FakeFeeds()), [])
        note = news.line_move_note({"open": -3.0, "spread": 3.0}, "Baltimore Ravens", "Cincinnati Bengals")
        self.assertEqual(note, "Line moved 6 toward Bengals since open (Ravens -3 → +3)")
        self.assertTrue(news.line_move_note({"open": -3.0, "spread": -3.0}, "Baltimore Ravens", "Cincinnati Bengals").startswith("Line steady"))
        self.assertIsNone(news.line_move_note({"spread": -3.0}, "A B", "C D"))


if __name__ == "__main__":
    unittest.main()
