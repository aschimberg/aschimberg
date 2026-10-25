"""Team and player news from free feeds, for context on the game cards.

Sources (all public feeds, checked from GitHub Actions; headlines link back
to the publisher):
    ESPN news API        league news, tagged with teams and athletes
    Google News RSS      one search per team: AP, USA Today, local papers, team sites
    CBS Sports, Yahoo Sports, ProFootballTalk (NFL), RotoWire player news

News never moves a line or a pick. Each headline gets a rough tone from a
word list (injury / suspension / benched read negative; return / cleared /
win streak read positive), and a player headline with injury words flags
that player's anytime-TD pick. Feeds are cached for NEWS_TTL_MIN minutes so
frequent refreshes don't hammer them; any feed that fails is skipped.

Turn it off with NEWS=off.
"""
import html
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from cfbmodel import store

from . import http

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
GOOGLE = "https://news.google.com/rss/search?"

SOURCES = {
    "nfl": {"espn": "football/nfl", "google_suffix": "NFL", "rss": [
        ("CBS Sports", "https://www.cbssports.com/rss/headlines/nfl/"),
        ("Yahoo Sports", "https://sports.yahoo.com/nfl/rss/"),
        ("ProFootballTalk", "https://www.nbcsports.com/profootballtalk.rss"),
        ("RotoWire", "https://www.rotowire.com/rss/news.php?sport=NFL")]},
    "nba": {"espn": "basketball/nba", "google_suffix": "NBA", "rss": [
        ("CBS Sports", "https://www.cbssports.com/rss/headlines/nba/"),
        ("Yahoo Sports", "https://sports.yahoo.com/nba/rss/"),
        ("RotoWire", "https://www.rotowire.com/rss/news.php?sport=NBA")]},
    "nhl": {"espn": "hockey/nhl", "google_suffix": "NHL", "rss": [
        ("CBS Sports", "https://www.cbssports.com/rss/headlines/nhl/"),
        ("RotoWire", "https://www.rotowire.com/rss/news.php?sport=NHL")]},
    "cfb": {"espn": "football/college-football", "google_suffix": "football", "rss": [
        ("CBS Sports", "https://www.cbssports.com/rss/headlines/college-football/"),
        ("RotoWire", "https://www.rotowire.com/rss/news.php?sport=CFB")]},
}

MAX_AGE_H = 72
PER_TEAM = 4

# betting promos, previews and recaps that say nothing new about a team
NOISE = re.compile(
    r"promo code|bonus (bets|funds)|sportsbook|kalshi|underdog|prizepicks|odds,? (picks|predictions)|best bets|player props"
    r"|parlay|how to watch|where to (watch|stream)|start time|tv channel|live updates|live score|highlights|fantasy|rankings"
    r"|mock draft|\bdfs\b|power rankings|winners? (&|and) (losers|sinners)|what (we|to) know|everything (you need )?to know"
    r"|live ?streams?|free stream|streaming|watch (today|live|free|online)|\bprediction|\bpicks\b|to watch\b|storylines|\breasons\b|pre-snap reads"
    r"|rooting guide|\bkeys? to\b|game updates|play-by-play|start or sit|\bwho wins\b|\bpreview\b|\bby the numbers\b",
    re.I)
# zero-width / full-width / "math" letters: the hallmarks of stream-spam titles
_SPAM_CHARS = re.compile("[\u200b-\u200f\u2060\uff01-\uff5e\U0001d400-\U0001d7ff]")


def spam(title, source=""):
    """Stream-spam and junk: odd unicode, non-Latin source names, mostly non-ASCII text."""
    if _SPAM_CHARS.search(title) or re.search(r"[\[\]!+@#$*]{3,}", title):
        return True
    if source and sum(ord(c) > 0x2FF for c in source) > len(source) / 3:
        return True
    letters = [c for c in title if c.isalpha()]
    return bool(letters) and sum(ord(c) > 0x2FF for c in letters) > len(letters) / 4

NEG = re.compile(
    r"\b(rule[sd]? out|out for|out indefinitely|injur(y|ies|ed)|questionable|doubtful|injured reserve|on ir|placed on ir|surgery|"
    r"sprain(ed)?|strain(ed)?|torn|tear|fracture[ds]?|concussion|illness|sick|suspend(ed|ion)|arrest(ed)?|benched|demoted|"
    r"fired|waived|released|limited|did not practice|dnp|setback|sidelined|inactive|game-time decision|blood clot|"
    r"slump|struggl\w*|losing streak|trade request|holdout|miss(es|ed)? (the )?(game|week|season|time))\b", re.I)
POS = re.compile(
    r"\b(return(s|ed|ing)? (to|from|for)|cleared|activated|healthy|full practice|full participant|upgraded|expected to play|"
    r"will play|available|extension|win(ning)? streak|dominant|breakout|career[- ]high|back (at|in) practice|off (the )?injury report)\b",
    re.I)
# an availability flag needs a status word, not just "injury update" (fantasy click-bait asks that every week)
INJ = re.compile(
    r"\b(rule[sd]? out|out for|questionable|doubtful|injured reserve|placed on ir|surgery|sprain(ed)?|torn|"
    r"concussion|illness|limited (in|at) practice|did not practice|dnp|sidelined|inactive|game-time decision|"
    r"not expected to play|day-to-day|downgraded)\b", re.I)
OK = re.compile(r"\b(cleared|activated|active for|available to play|expected to play|will play|"
                r"off (the )?injury report|no injury designation)\b", re.I)
OK_WEAK = re.compile(r"\b(full practice|full participant|practiced fully)\b", re.I)  # loses to an official status


def enabled():
    return os.environ.get("NEWS", "on").lower() != "off"


def ttl_minutes():
    return float(os.environ.get("NEWS_TTL_MIN", "90"))


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_TZ = {"PDT": -7, "PST": -8, "MDT": -6, "MST": -7, "CDT": -5, "CST": -6, "EDT": -4, "EST": -5, "GMT": 0, "UTC": 0}


def parse_date(s):
    """RSS / ISO date -> ISO UTC string, or None. Handles RotoWire's '6:10:00 AM PDT'."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"\w{3}, (\d{1,2} \w{3} \d{4}) (\d{1,2}):(\d{2}):(\d{2}) ([AP]M) (\w+)", s)
    if m:  # RotoWire: 'Sun, 25 Oct 2026 6:10:00 AM PDT' (the email parser drops the AM/PM and zone)
        try:
            d = datetime.strptime(m.group(1), "%d %b %Y")
            h = int(m.group(2)) % 12 + (12 if m.group(5) == "PM" else 0)
            return _iso(d.replace(hour=h, minute=int(m.group(3)), second=int(m.group(4)),
                                  tzinfo=timezone(timedelta(hours=_TZ.get(m.group(6), 0)))))
        except ValueError:
            return None
    try:
        if "T" in s[:20] and s[:4].isdigit():
            return _iso(datetime.fromisoformat(s.replace("Z", "+00:00")))
        return _iso(parsedate_to_datetime(s))
    except (TypeError, ValueError, IndexError):
        return None


def _text(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(s or "")))
    return re.sub(r"\s+", " ", s).strip()


def tone(text):
    """-1 (negative), 0 or +1 from the word lists."""
    n, p = len(NEG.findall(text or "")), len(POS.findall(text or ""))
    return (p > n) - (n > p)


def parse_rss(body, source, google=False):
    root = ET.fromstring(body if isinstance(body, bytes) else body.encode("utf-8"))
    out = []
    for it in root.iter("item"):
        title = _text(it.findtext("title"))
        src = source
        if google:
            s = it.find("source")
            if s is not None and (s.text or "").strip():
                src = s.text.strip()
            if src and title.endswith(" - " + src):
                title = title[: -len(src) - 3]
            summary = ""  # Google's description just repeats the title as a link
        else:
            summary = _text(it.findtext("description"))
            summary = re.sub(r"\s*Visit RotoWire.*$", "", summary)
        if not title:
            continue
        out.append({"t": title, "u": (it.findtext("link") or "").strip(), "src": src,
                    "at": parse_date(it.findtext("pubDate")), "sum": summary[:220]})
    return out


def parse_espn(doc):
    out = []
    for a in doc.get("articles") or []:
        cats = a.get("categories") or []
        link = ((a.get("links") or {}).get("web") or {}).get("href")
        out.append({"t": _text(a.get("headline")), "u": link, "src": "ESPN", "at": parse_date(a.get("published")),
                    "sum": _text(a.get("description"))[:220],
                    "team_names": [c.get("description") for c in cats if c.get("type") == "team" and c.get("description")],
                    "names": [c.get("description") or (c.get("athlete") or {}).get("description")
                              for c in cats if c.get("type") == "athlete"]})
    return out


def google_url(query):
    return GOOGLE + urllib.parse.urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})


def _key(title):
    return re.sub(r"[^a-z0-9]", "", title.lower())[:80]


def _team_terms(team_info):
    """{abbr: {'name': 'Baltimore Ravens', 'terms': [...]}} -> {abbr: compiled regex}."""
    out = {}
    for ab, info in team_info.items():
        terms = [info["name"]] + list(info.get("terms") or [])
        out[ab] = re.compile(r"\b(" + "|".join(re.escape(t) for t in terms if t) + r")\b", re.I)
    return out


def tag_teams(items, team_info):
    rx = _team_terms(team_info)
    by_name = {info["name"].lower(): ab for ab, info in team_info.items()}
    for it in items:
        tags = set(it.get("teams") or [])
        for n in it.get("team_names") or []:
            if n.lower() in by_name:
                tags.add(by_name[n.lower()])
        # RotoWire summaries name the opponent ("...against the Lions"), so only its headline can tag a team
        text = it["t"] if it.get("src") == "RotoWire" else it["t"] + " " + it.get("sum", "")
        tags.update(ab for ab, r in rx.items() if r.search(text))
        it["teams"] = sorted(tags)
        it.pop("team_names", None)
    return items


def _get(url, fetcher):
    if fetcher:
        return fetcher(url)
    return http.fetch(url, retries=1, timeout=30, headers={"User-Agent": "Mozilla/5.0 (ASPicks model; news)"})


def fetch_all(sport, team_info, log=print, fetcher=None, pause=0.3):
    """Fresh items from every source for the given teams (failures are logged and skipped)."""
    cfg = SOURCES[sport]
    items, ok = [], []
    try:
        doc = json.loads(_get(f"{ESPN}/{cfg['espn']}/news?limit=100", fetcher))
        items += parse_espn(doc)
        ok.append("ESPN")
    except Exception as e:
        log(f"News {sport}: ESPN failed ({e})")
    for name, url in cfg["rss"]:
        try:
            items += parse_rss(_get(url, fetcher), name)
            ok.append(name)
        except Exception as e:
            log(f"News {sport}: {name} failed ({e})")
    g_ok = 0
    for ab, info in team_info.items():
        try:
            rows = parse_rss(_get(google_url(f'"{info["name"]}" {cfg["google_suffix"]} when:3d'), fetcher), "Google News", google=True)
            for r in rows[:25]:
                r["teams"] = [ab]
            items += rows[:25]
            g_ok += 1
        except Exception as e:
            log(f"News {sport}: Google News {info['name']} failed ({e})")
        if pause and not fetcher:
            time.sleep(pause)
    if g_ok:
        ok.append(f"Google News ({g_ok} teams)")
    log(f"News {sport}: {len(items)} headlines from {', '.join(ok) or 'no sources'}")
    return items


def collect(sport, team_info, now, cache_path, log=print, fetcher=None):
    """Recent, de-duplicated, team-tagged headlines (cached for ttl_minutes()).

    team_info: {abbr: {'name': full name, 'terms': [other names to match, e.g. nickname]}}
    """
    if not enabled():
        return []
    cache = store.load(cache_path, {"fetched_at": None, "items": []})
    fresh = cache.get("fetched_at") and (
        now - datetime.fromisoformat(cache["fetched_at"].replace("Z", "+00:00"))) < timedelta(minutes=ttl_minutes())
    items = list(cache.get("items") or [])
    if not fresh or set(team_info) - set(cache.get("teams") or []):
        new = fetch_all(sport, team_info, log=log, fetcher=fetcher)
        if new:
            items = new + items
            cache["fetched_at"] = _iso(now)
    cutoff = _iso(now - timedelta(hours=MAX_AGE_H))
    seen, keep = set(), []
    for it in sorted(items, key=lambda x: x.get("at") or "", reverse=True):
        k = _key(it["t"])
        if (not it.get("at") or it["at"] < cutoff or it["at"] > _iso(now + timedelta(hours=1)) or k in seen
                or NOISE.search(it["t"]) or spam(it["t"], it.get("src", ""))):
            continue
        seen.add(k)
        keep.append(it)
    tag_teams(keep, team_info)
    keep = [it for it in keep if it["teams"] or it.get("names") or it["src"] == "RotoWire"]  # RotoWire = player news
    for it in keep:
        it["tone"] = tone(it["t"] + " " + it.get("sum", ""))
    # keep the file small: newest 25 per team plus anything naming a player
    per, slim = {}, []
    for it in keep:
        if any(per.get(t, 0) < 25 for t in it["teams"]) or it.get("names") or it["src"] == "RotoWire":
            slim.append(it)
            for t in it["teams"]:
                per[t] = per.get(t, 0) + 1
    store.save(cache_path, {"fetched_at": cache.get("fetched_at"), "teams": sorted(team_info), "items": slim}, compact=True)
    return slim


def for_team(items, abbr, n=PER_TEAM):
    rows = [it for it in items if abbr in it["teams"]]
    # stories about this team alone first, then matchup stories, then round-ups
    rows.sort(key=lambda it: (min(len(it["teams"]), 3), -(datetime.fromisoformat(it["at"].replace("Z", "+00:00")).timestamp())))
    return [{"t": it["t"], "u": it["u"], "src": it["src"], "at": it["at"], "tone": it["tone"]} for it in rows[:n]]


def team_tone(items, abbr):
    """Average tone of this team's recent headlines (None if none)."""
    xs = [it["tone"] for it in items if abbr in it["teams"]]
    return round(sum(xs) / len(xs), 2) if xs else None


def _name_rx(name):
    parts = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", name, flags=re.I).replace(".", "").split()
    if len(parts) < 2:
        return None
    return re.compile(r"\b" + re.escape(parts[0]) + r"\.?\s+" + re.escape(parts[-1]) + r"\b", re.I)


def player_note(items, name, team=None):
    """Newest headline about this player with injury/availability news:
    {'flag': 'warn'|'ok', 't', 'u', 'src', 'at'} or None."""
    rx = _name_rx(name)
    if not rx:
        return None
    for it in items:  # newest first
        if team and it["teams"] and team not in it["teams"]:
            continue
        text = it["t"] + " " + it.get("sum", "")
        if not (rx.search(text) or name in (it.get("names") or [])):
            continue
        if OK.search(text):
            flag = "ok"
        elif INJ.search(text):
            flag = "warn"
        elif OK_WEAK.search(text):
            flag = "ok"
        else:
            continue
        return {"flag": flag, "t": it["t"], "u": it["u"], "src": it["src"], "at": it["at"]}
    return None


def line_move_note(line, home_name, away_name, fmt=lambda v: f"{v:+g}"):
    """'Line moved 6 toward Bengals since open (Ravens -3 → +3)' from a card's line, or None."""
    if not line or line.get("open") is None or line.get("spread") is None:
        return None
    mv = line["spread"] - line["open"]
    if abs(mv) < 0.5:
        return f"Line steady since open ({home_name.split()[-1]} {fmt(line['spread'])})"
    toward = away_name if mv > 0 else home_name
    return (f"Line moved {abs(mv):g} toward {toward.split()[-1]} since open "
            f"({home_name.split()[-1]} {fmt(line['open'])} → {fmt(line['spread'])})")
