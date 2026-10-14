"""The Odds API (the-odds-api.com, v4) client with a credit budget.

Active only when ODDS_API_KEY is set. Each odds call costs
markets x regions credits (the free plan has 500 a month), so responses are
cached per sport and refetched only when older than max_age_hours, and calls
stop once the remaining balance falls to ODDS_API_RESERVE.
"""
import json
import os
import statistics
import unicodedata
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from cfbmodel import store
from sportscommon import http

BASE = "https://api.the-odds-api.com/v4"
ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "oddsapi"
SPORT_KEYS = {"nfl": "americanfootball_nfl", "nhl": "icehockey_nhl", "cfb": "americanfootball_ncaaf"}
# Books preferred for the "main" price (first one present wins); everything is used for consensus.
PREFERRED = [b.strip() for b in os.environ.get("ODDS_API_BOOKS", "draftkings,fanduel,betmgm,caesars").split(",") if b.strip()]


def api_key():
    return os.environ.get("ODDS_API_KEY", "").strip() or None


def reserve():
    return int(os.environ.get("ODDS_API_RESERVE", "40"))


def _utcnow():
    return datetime.now(timezone.utc)


def _usage_path():
    return CACHE_DIR / "usage.json"


def usage():
    return store.load(_usage_path(), {})


def get_odds(sport, markets, max_age_hours=12, now=None, log=print, fetcher=None):
    """Events for sport ('nfl'|'nhl'|'cfb'), from cache when fresh. None if unavailable."""
    now = now or _utcnow()
    key = api_key()
    if not key:
        return None
    cache_p = CACHE_DIR / f"{sport}.json"
    cached = store.load(cache_p, None)
    if cached and cached.get("markets") != markets:
        cached = None
    age = None
    if cached:
        age = (now - datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))).total_seconds() / 3600
        if age < max_age_hours:
            return cached["events"]
    stale_ok = cached["events"] if cached and age is not None and age < 24 else None
    u = usage()
    if u.get("remaining") is not None and u["remaining"] <= reserve():
        log(f"Odds API: {u['remaining']} credits left (reserve {reserve()}); using cached {sport} odds if <24h old")
        return stale_ok
    q = urllib.parse.urlencode({"apiKey": key, "regions": "us", "markets": markets,
                                "oddsFormat": "american", "dateFormat": "iso"})
    url = f"{BASE}/sports/{SPORT_KEYS[sport]}/odds?{q}"
    try:
        body, headers = (fetcher or (lambda u_: http.fetch(u_, retries=2, timeout=30, want_headers=True)))(url)
    except urllib.error.HTTPError as e:
        log(f"Odds API {sport}: HTTP {e.code}" + (" (check ODDS_API_KEY)" if e.code == 401 else ""))
        return stale_ok
    except Exception as e:
        log(f"Odds API {sport}: {e}")
        return stale_ok
    events = json.loads(body)
    h = {k.lower(): v for k, v in headers.items()}
    rem = h.get("x-requests-remaining")
    store.save(_usage_path(), {"remaining": int(float(rem)) if rem not in (None, "") else None,
                               "used": h.get("x-requests-used"), "last_cost": h.get("x-requests-last"),
                               "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    store.save(cache_p, {"fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "markets": markets, "events": events})
    log(f"Odds API {sport}: {len(events)} events, cost {h.get('x-requests-last')}, {rem} credits left")
    return events


def team_key(name):
    """'Montréal Canadiens' / 'St. Louis Blues' -> 'montrealcanadiens' / 'stlouisblues'."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in s if ch.isalnum())


def find_event(events, home_name, away_name, start_iso, tolerance_hours=30):
    """Match an Odds API event by full team names and start time."""
    if not events:
        return None
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00")) if start_iso else None
    hn, an = team_key(home_name), team_key(away_name)
    for e in events:
        if team_key(e.get("home_team")) == hn and team_key(e.get("away_team")) == an:
            if start is None:
                return e
            ct = datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
            if abs((ct - start).total_seconds()) <= tolerance_hours * 3600:
                return e
    return None


def _outcomes(event, market):
    """[(book_key, book_title, {name: outcome})] for one market."""
    out = []
    for b in event.get("bookmakers", []):
        for m in b.get("markets", []):
            if m.get("key") == market:
                out.append((b["key"], b.get("title", b["key"]), {o["name"]: o for o in m.get("outcomes", [])}))
    return out


def _main_book(rows):
    order = {k: i for i, k in enumerate(PREFERRED)}
    return min(rows, key=lambda r: order.get(r[0], 99)) if rows else None


def spreads(event):
    """Consensus and per-book home spread (home perspective, - = home favored)."""
    home, away = event["home_team"], event["away_team"]
    rows = [r for r in _outcomes(event, "spreads") if home in r[2] and away in r[2] and r[2][home].get("point") is not None]
    if not rows:
        return None
    pts = [r[2][home]["point"] for r in rows]
    main = _main_book(rows)
    cons = statistics.median(pts)
    if cons * 2 != int(cons * 2):  # even split between two numbers: defer to the main book
        cons = main[2][home]["point"]
    best_home = max(rows, key=lambda r: (r[2][home]["point"], r[2][home]["price"]))
    best_away = max(rows, key=lambda r: (r[2][away]["point"], r[2][away]["price"]))
    return {
        "consensus": cons, "n_books": len(rows),
        "main": {"book": main[1], "spread": main[2][home]["point"], "home_odds": main[2][home]["price"], "away_odds": main[2][away]["price"]},
        "best_home": {"book": best_home[1], "point": best_home[2][home]["point"], "price": best_home[2][home]["price"]},
        "best_away": {"book": best_away[1], "point": best_away[2][away]["point"], "price": best_away[2][away]["price"]},
    }


def totals(event):
    rows = [r for r in _outcomes(event, "totals") if "Over" in r[2] and "Under" in r[2]]
    if not rows:
        return None
    main = _main_book(rows)
    return {"consensus": statistics.median(r[2]["Over"]["point"] for r in rows), "n_books": len(rows),
            "main": {"book": main[1], "total": main[2]["Over"]["point"], "over_odds": main[2]["Over"]["price"],
                     "under_odds": main[2]["Under"]["price"]}}


def moneylines(event):
    home, away = event["home_team"], event["away_team"]
    rows = [r for r in _outcomes(event, "h2h") if home in r[2] and away in r[2]]
    if not rows:
        return None
    main = _main_book(rows)
    best_home = max(rows, key=lambda r: r[2][home]["price"])
    best_away = max(rows, key=lambda r: r[2][away]["price"])
    return {"n_books": len(rows),
            "main": {"book": main[1], "home": main[2][home]["price"], "away": main[2][away]["price"]},
            "best_home": {"book": best_home[1], "price": best_home[2][home]["price"]},
            "best_away": {"book": best_away[1], "price": best_away[2][away]["price"]}}


# --- player props (event-level odds) ------------------------------------------
PROP_MARKETS = {"nfl": "player_anytime_td"}
_props_fetched = {"n": 0}  # per-process (per refresh run) count of credit-costing prop calls


def props_per_run():
    return int(os.environ.get("ODDS_API_PROPS_PER_RUN", "4"))


def events(sport, log=print, fetcher=None):
    """Upcoming events (ids, teams, start). The /events endpoint doesn't use credits."""
    key = api_key()
    if not key:
        return None
    url = f"{BASE}/sports/{SPORT_KEYS[sport]}/events?" + urllib.parse.urlencode({"apiKey": key, "dateFormat": "iso"})
    try:
        body, _ = (fetcher or (lambda u_: http.fetch(u_, retries=2, timeout=30, want_headers=True)))(url)
        return json.loads(body)
    except Exception as e:
        log(f"Odds API {sport} events: {e}")
        return None


def event_props(sport, event, max_age_hours=6, now=None, log=print, fetcher=None):
    """Anytime-scorer odds for one event, cached; costs 1 credit (1 market x 1 region) when fetched.

    Respects ODDS_API_PROPS_PER_RUN and the credit reserve; returns the cached
    copy (if any) when it won't fetch.
    """
    now = now or _utcnow()
    market = PROP_MARKETS[sport]
    path = CACHE_DIR / f"props_{sport}.json"
    cache = store.load(path, {})
    ent = cache.get(event["id"])
    if ent:
        age = (now - datetime.fromisoformat(ent["fetched_at"].replace("Z", "+00:00"))).total_seconds() / 3600
        if age < max_age_hours:
            return ent["data"]
    key = api_key()
    if not key:
        return ent["data"] if ent else None
    u = usage()
    if (u.get("remaining") is not None and u["remaining"] <= reserve()) or _props_fetched["n"] >= props_per_run():
        return ent["data"] if ent else None
    q = urllib.parse.urlencode({"apiKey": key, "regions": "us", "markets": market, "oddsFormat": "american",
                                "dateFormat": "iso"})
    url = f"{BASE}/sports/{SPORT_KEYS[sport]}/events/{event['id']}/odds?{q}"
    try:
        body, headers = (fetcher or (lambda u_: http.fetch(u_, retries=2, timeout=30, want_headers=True)))(url)
    except Exception as e:
        log(f"Odds API {sport} props {event['id']}: {e}")
        return ent["data"] if ent else None
    _props_fetched["n"] += 1
    data = json.loads(body)
    h = {k.lower(): v for k, v in headers.items()}
    rem = h.get("x-requests-remaining")
    store.save(_usage_path(), {"remaining": int(float(rem)) if rem not in (None, "") else None,
                               "used": h.get("x-requests-used"), "last_cost": h.get("x-requests-last"),
                               "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    # keep only upcoming events' props so the file stays small
    cache = {k: v for k, v in cache.items() if v.get("commence_time", "") >= (now.strftime("%Y-%m-%dT%H:%M:%SZ"))[:10]}
    cache[event["id"]] = {"fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "commence_time": event.get("commence_time"),
                          "data": data}
    store.save(path, cache, compact=True)
    log(f"Odds API {sport} props {event.get('away_team')} @ {event.get('home_team')}: cost {h.get('x-requests-last')}, {rem} left")
    return data
