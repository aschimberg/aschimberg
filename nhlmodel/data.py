"""Season data with an incremental cache of parsed games (gzipped JSON)."""
import gzip
import json

from . import api, config, shots

DONE_STATES = {"OFF", "FINAL"}


def _path(season):
    return config.CACHE_DIR / f"games_{season}.json.gz"


def load_cache(season):
    p = _path(season)
    if not p.exists():
        return {"season": season, "complete": False, "games": {}}
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def save_cache(doc):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(_path(doc["season"]), "wt", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))


def team_list():
    out = {}
    for s in api.standings():
        ab = s["teamAbbrev"]["default"]
        out[ab] = {"abbr": ab, "name": s["teamName"]["default"], "conference": s.get("conferenceName"),
                   "division": s.get("divisionName"), "logo": s.get("teamLogo"),
                   "record": f"{s.get('wins', 0)}-{s.get('losses', 0)}-{s.get('otLosses', 0)}",
                   "points": s.get("points"), "gp": s.get("gamesPlayed")}
    return out


def season_schedule(season, teams):
    """All games (regular + playoffs) from each club's season schedule, deduped."""
    games = {}
    for t in teams:
        try:
            for g in api.club_schedule(t, season):
                if g.get("gameType") not in (2, 3):
                    continue
                games[g["id"]] = {
                    "id": g["id"], "type": g["gameType"], "start": g.get("startTimeUTC"), "date": g.get("gameDate"),
                    "home": g["homeTeam"]["abbrev"], "away": g["awayTeam"]["abbrev"], "state": g.get("gameState"),
                    "home_score": g["homeTeam"].get("score"), "away_score": g["awayTeam"].get("score"),
                    "venue": (g.get("venue") or {}).get("default"),
                    "neutral": bool(g.get("neutralSite")),
                }
        except Exception:
            continue
    return games


def update_season(season, teams, log=print, max_new=None):
    """Fetch play-by-play for completed games not yet cached. Returns (cache, schedule)."""
    doc = load_cache(season)
    if doc.get("complete"):
        return doc, {}
    sched = season_schedule(season, teams)
    todo = [g for g in sched.values() if g["state"] in DONE_STATES and str(g["id"]) not in doc["games"]]
    todo.sort(key=lambda g: g["start"] or "")
    if max_new:
        todo = todo[:max_new]
    for i, g in enumerate(todo):
        try:
            doc["games"][str(g["id"])] = shots.parse(api.play_by_play(g["id"]))
        except Exception as e:
            log(f"pbp {g['id']} failed: {e}")
        if (i + 1) % 200 == 0:
            log(f"  {season}: parsed {i + 1}/{len(todo)} games")
            save_cache(doc)
    remaining = [g for g in sched.values() if g["state"] not in DONE_STATES]
    if sched and not remaining and len(doc["games"]) >= 0.98 * len(sched):
        doc["complete"] = True
    if todo:
        save_cache(doc)
        log(f"NHL {season}: +{len(todo)} games (cached {len(doc['games'])})")
    return doc, sched
