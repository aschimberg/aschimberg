"""NBA season caches built from ESPN (data/nba/cache/season_<year>.json).

Each season file keeps every game (scores, the ESPN/DraftKings line last seen
before tip-off) plus, for finished games, the box score: team possessions,
player minutes and Game Score, and the closing odds from ESPN's pickcenter.
Dates whose games are all final are frozen and never re-fetched.
"""
import time
from datetime import date, timedelta

from cfbmodel import store

from . import config, espn


def cache_path(season):
    return config.CACHE_DIR / f"season_{season}.json"


def load_cache(season):
    return store.load(cache_path(season), {"season": season, "games": {}, "final_dates": [], "teams": {}, "calendar": []})


def season_window(season):
    """Fallback date range when ESPN's calendar is unavailable: Oct 1 - Jun 30."""
    return date(season - 1, 10, 1), date(season, 6, 30)


def merge_game(old, new):
    """Keep box score / close / the last pre-game line when a scoreboard refresh comes in."""
    if not old:
        return new
    g = dict(old)
    for k, v in new.items():
        if k == "odds":
            # scoreboards for finished games drop odds; keep what we saw before tip
            if v:
                g["odds"] = v
        elif v is not None or k not in g:
            g[k] = v
    return g


def update_season(season, today, log=print, fetcher=None, box_budget=None, pause=None, through=None):
    """Bring a season's cache up to date. Returns the cache doc.

    today: ET date. through: last date to fetch (default today + slate days).
    box_budget: max box scores to fetch this run (None = no limit).
    """
    pause = config.REQUEST_PAUSE if pause is None else pause
    doc = load_cache(season)
    final = set(doc.get("final_dates") or [])
    lo, hi = season_window(season)
    through = through or min(hi, today + timedelta(days=config.SLATE_DAYS))
    cal = [date.fromisoformat(d) for d in doc.get("calendar") or []]
    if not cal:
        # ask about a date inside the season so ESPN returns that season's calendar
        probe_day = date(season, 1, 15) if today > hi else max(lo, min(today, hi))
        try:
            probe = espn.scoreboard(probe_day, fetcher)
            cal = espn.calendar_dates(probe)
        except Exception as e:
            log(f"NBA {season}: calendar unavailable ({e}); scanning every day")
        if cal:
            doc["calendar"] = [d.isoformat() for d in cal]
    days = [d for d in (cal or [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]) if lo <= d <= through]
    fetched = 0
    for d in days:
        if d.isoformat() in final:
            continue
        try:
            sb = espn.scoreboard(d, fetcher)
        except Exception as e:
            log(f"NBA scoreboard {d}: {e}")
            continue
        fetched += 1
        day_games = []
        for e in sb.get("events") or []:
            g, teams = espn.parse_event(e)
            if not g or g["season"] != season:
                continue
            doc["games"][g["id"]] = merge_game(doc["games"].get(g["id"]), g)
            day_games.append(g)
            for ab, info in teams.items():
                cur = doc["teams"].get(ab, {})
                doc["teams"][ab] = {**cur, **{k: v for k, v in info.items() if v is not None}}
        if d < today - timedelta(days=1) and all(g["completed"] for g in day_games):
            final.add(d.isoformat())
        if pause:
            time.sleep(pause)
    doc["final_dates"] = sorted(final)
    need = sorted((g for g in doc["games"].values() if g["completed"] and "box" not in g), key=lambda g: g["start"] or "")
    if box_budget is not None:
        need = need[:box_budget]
    boxed = 0
    for g in need:
        try:
            s = espn.parse_summary(espn.summary(g["id"], fetcher), g["home"], g["away"])
        except Exception as e:
            log(f"NBA box {g['id']}: {e}")
            continue
        g["box"] = {"team": s["team"], "players": s["players"]}
        if s["close"]:
            g["close"] = s["close"]
        boxed += 1
        if pause:
            time.sleep(pause)
    left = sum(1 for g in doc["games"].values() if g["completed"] and "box" not in g)
    store.save(cache_path(season), doc, compact=True)
    log(f"NBA {season}: {len(doc['games'])} games cached ({fetched} scoreboards, {boxed} box scores fetched"
        + (f", {left} still to fetch" if left else "") + ")")
    return doc


def name_map(teams):
    """{'Boston Celtics': 'BOS', ...} for matching the injury report."""
    return {v.get("name"): k for k, v in teams.items() if v.get("name")}
