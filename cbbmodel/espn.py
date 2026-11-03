"""ESPN men's college basketball: D1 teams, schedules, box-score possessions and betting lines."""
import json
from concurrent.futures import ThreadPoolExecutor

from sportscommon.http import fetch_json

from . import config


def _get(url):
    try:
        return fetch_json(url, retries=3, timeout=60)
    except Exception as e:  # one bad game shouldn't stop a backfill
        print(f"  skip {url}: {str(e)[:120]}")
        return None


def d1_teams():
    doc = _get(f"{config.SITE}/teams?groups=50&limit=500") or {}
    teams = ((doc.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []
    out = {}
    for t in teams:
        t = t.get("team") or t
        out[str(t["id"])] = {"name": t.get("location") or t.get("displayName"), "abbr": t.get("abbreviation"),
                             "full": t.get("displayName")}
    return out


def _score(c):
    s = c.get("score")
    if isinstance(s, dict):
        s = s.get("value", s.get("displayValue"))
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _game(ev, season):
    comp = ev["competitions"][0]
    side = {c["homeAway"]: c for c in comp.get("competitors", [])}
    if "home" not in side or "away" not in side:
        return None
    st = (comp.get("status") or ev.get("status") or {}).get("type") or {}
    return {
        "id": str(ev["id"]), "season": season, "start": ev.get("date"),
        "h": str(side["home"]["id"]), "a": str(side["away"]["id"]),
        "neutral": bool(comp.get("neutralSite")), "done": bool(st.get("completed")),
        "hs": _score(side["home"]), "as": _score(side["away"]),
    }


def schedule(team_id, season):
    """Regular-season and postseason games for one team (ESPN season = the year it ends)."""
    games = []
    for stype in (2, 3):
        doc = _get(f"{config.SITE}/teams/{team_id}/schedule?season={season}&seasontype={stype}") or {}
        games += [g for g in (_game(e, season) for e in doc.get("events", [])) if g]
    return games


def season_games(season, teams):
    """Every game involving a D1 team, deduplicated across team schedules."""
    with ThreadPoolExecutor(config.THREADS) as ex:
        lists = list(ex.map(lambda t: schedule(t, season), list(teams)))
    games = {}
    for lst in lists:
        for g in lst:
            games[g["id"]] = g
    return sorted(games.values(), key=lambda g: (g["start"] or "", g["id"]))


def _stat(stats, name):
    for s in stats:
        if s.get("name") == name:
            return s.get("displayValue")
    return None


def box(event_id):
    """Possessions and four-factor counts per team from the box score: {team_id: {...}}."""
    doc = _get(f"{config.SITE}/summary?event={event_id}") or {}
    out = {}
    for t in ((doc.get("boxscore") or {}).get("teams") or []):
        st = t.get("statistics") or []
        try:
            fgm, fga = (int(x) for x in _stat(st, "fieldGoalsMade-fieldGoalsAttempted").split("-"))
            tpm, tpa = (int(x) for x in _stat(st, "threePointFieldGoalsMade-threePointFieldGoalsAttempted").split("-"))
            ftm, fta = (int(x) for x in _stat(st, "freeThrowsMade-freeThrowsAttempted").split("-"))
            orb = int(_stat(st, "offensiveRebounds") or 0)
            drb = int(_stat(st, "defensiveRebounds") or 0)
            tov = int(_stat(st, "totalTurnovers") or _stat(st, "turnovers") or 0)
        except (AttributeError, ValueError, TypeError):
            continue
        out[str(t["team"]["id"])] = {"fgm": fgm, "fga": fga, "tpm": tpm, "tpa": tpa, "ftm": ftm, "fta": fta,
                                     "orb": orb, "drb": drb, "tov": tov,
                                     "poss": round(fga - orb + tov + 0.475 * fta, 1)}
    return out


def parse_odds(o, home_id=None):
    """Home spread, total and moneylines from an ESPN odds object (scoreboard or core API)."""
    ho, ao = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}
    spread, total = o.get("spread"), o.get("overUnder")
    if spread is not None:
        spread = float(spread)
        if ho.get("favorite") is True:
            spread = -abs(spread)
        elif ao.get("favorite") is True:
            spread = abs(spread)
    return {"spread": spread, "total": float(total) if total is not None else None,
            "h_ml": ho.get("moneyLine"), "a_ml": ao.get("moneyLine"),
            "book": ((o.get("provider") or {}).get("name"))}


def closing_odds(event_id):
    """Pre-game line ESPN kept for a finished game (core API), preferring the listed books."""
    doc = _get(f"{config.CORE}/events/{event_id}/competitions/{event_id}/odds") or {}
    items = {str((it.get("provider") or {}).get("id")): it for it in doc.get("items") or []}
    for pid in config.PROVIDERS:
        if pid in items and items[pid].get("spread") is not None:
            return parse_odds(items[pid])
    return None


def scoreboard(day):
    """Games on a date (YYYYMMDD) with the current line when ESPN has one."""
    doc = _get(f"{config.SITE}/scoreboard?dates={day}&groups=50&limit=500") or {}
    out = []
    for ev in doc.get("events", []):
        g = _game(ev, None)
        if not g:
            continue
        odds = (ev["competitions"][0].get("odds") or [])
        odds = [o for o in odds if str((o.get("provider") or {}).get("id")) != "59"]
        if odds:
            g.update(parse_odds(odds[0]))
        out.append(g)
    return out


def fill(games, with_odds=lambda g: False):
    """Add box-score possessions (and closing lines where asked) to finished games, in parallel."""
    todo = [g for g in games if g["done"] and "hp" not in g]

    def one(g):
        b = box(g["id"])
        if g["h"] in b and g["a"] in b:
            g["hp"], g["ap"] = b[g["h"]]["poss"], b[g["a"]]["poss"]
            g["hb"], g["ab"] = b[g["h"]], b[g["a"]]
        if with_odds(g) and "spread" not in g:
            o = closing_odds(g["id"])
            if o:
                g.update(o)
        return g

    with ThreadPoolExecutor(config.THREADS) as ex:
        list(ex.map(one, todo))
    return games


def dumps(games):
    return json.dumps(games, separators=(",", ":"))
