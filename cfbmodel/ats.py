"""Historical ATS records and situational trends per team."""
import math
from collections import defaultdict
from datetime import datetime

from . import config

LABELS = {
    "overall": "Overall",
    "fav": "As favorite",
    "dog": "As underdog",
    "home": "At home",
    "away": "On the road",
    "neutral": "Neutral site",
    "home_fav": "Home favorite",
    "home_dog": "Home underdog",
    "away_fav": "Road favorite",
    "away_dog": "Road underdog",
    "conf": "Conference games",
    "nonconf": "Non-conference",
    "off_bye": "Off a bye",
    "last10": "Last 10",
}


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def history_record(game, spread):
    """Compact record stored in the history cache."""
    return {
        "id": game["id"],
        "season": game["season"],
        "week": game["week"],
        "season_type": game["season_type"],
        "start": game["start"],
        "neutral": game["neutral"],
        "conference_game": game["conference_game"],
        "home": game["home"],
        "away": game["away"],
        "home_pts": game["home_pts"],
        "away_pts": game["away_pts"],
        "completed": game["completed"],
        "spread": spread,
    }


def previous_game_dates(games):
    """{(team, game_id): days since that team's previous game in the season}."""
    by_team = defaultdict(list)
    for gm in games:
        dt = parse_dt(gm.get("start"))
        if dt is None:
            continue
        for t in (gm["home"], gm["away"]):
            by_team[(t, gm["season"])].append((dt, gm["id"]))
    rest = {}
    for (team, _season), items in by_team.items():
        items.sort()
        for i, (dt, gid) in enumerate(items):
            rest[(team, gid)] = (dt - items[i - 1][0]).days if i > 0 else None
    return rest


def situations(team, game, team_spread, rest_days):
    is_home = game["home"] == team
    venue = "neutral" if game.get("neutral") else ("home" if is_home else "away")
    cats = ["overall", venue]
    role = "fav" if team_spread < 0 else ("dog" if team_spread > 0 else None)
    if role:
        cats.append(role)
        if venue != "neutral":
            cats.append(f"{venue}_{role}")
    cats.append("conf" if game.get("conference_game") else "nonconf")
    if rest_days is not None and rest_days >= config.BYE_DAYS:
        cats.append("off_bye")
    return cats


def ats_result(team_margin, team_spread):
    adj = team_margin + team_spread
    if abs(adj) < 1e-9:
        return "P"
    return "W" if adj > 0 else "L"


def compute_trends(games):
    """games: history records (any seasons). Returns {team: {cat: [w, l, p]}}."""
    rest = previous_game_dates(games)
    trends = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    recent = defaultdict(list)
    ordered = sorted(games, key=lambda x: x.get("start") or "")
    for gm in ordered:
        if not gm.get("completed") or gm.get("spread") is None:
            continue
        if gm.get("home_pts") is None or gm.get("away_pts") is None:
            continue
        for team in (gm["home"], gm["away"]):
            is_home = gm["home"] == team
            ts = gm["spread"] if is_home else -gm["spread"]
            margin = (gm["home_pts"] - gm["away_pts"]) * (1 if is_home else -1)
            res = ats_result(margin, ts)
            idx = "WLP".index(res)
            for c in situations(team, gm, ts, rest.get((team, gm["id"]))):
                trends[team][c][idx] += 1
            recent[team].append(idx)
    for team, idxs in recent.items():
        rec = [0, 0, 0]
        for i in idxs[-10:]:
            rec[i] += 1
        trends[team]["last10"] = rec
    return {t: dict(v) for t, v in trends.items()}


def summarize(rec):
    w, l, p = rec
    n = w + l
    pct = w / n if n else None
    z = (w - n / 2) / math.sqrt(n / 4) if n else 0.0
    return {"w": w, "l": l, "p": p, "pct": round(pct, 3) if pct is not None else None, "z": round(z, 2)}


def is_notable(s):
    n = s["w"] + s["l"]
    return (
        n >= config.TREND_MIN_GAMES
        and s["pct"] is not None
        and (s["pct"] >= config.TREND_NOTABLE_PCT or s["pct"] <= 1 - config.TREND_NOTABLE_PCT)
    )


def relevant_trends(trends, team, game, team_spread, rest_days):
    """Trends that apply to `team` in this specific game situation."""
    t = trends.get(team, {})
    cats = ["overall"]
    if team_spread is not None:
        cats = situations(team, game, team_spread, rest_days)
    else:
        venue = "neutral" if game.get("neutral") else ("home" if game["home"] == team else "away")
        cats += [venue, "conf" if game.get("conference_game") else "nonconf"]
        if rest_days is not None and rest_days >= config.BYE_DAYS:
            cats.append("off_bye")
    cats.append("last10")
    out = []
    for c in cats:
        if c == "nonconf" or c not in t:
            continue
        s = summarize(t[c])
        s.update({"key": c, "label": LABELS[c], "notable": is_notable(s) and c != "last10"})
        out.append(s)
    return out
