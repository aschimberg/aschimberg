"""Starting-QB adjustment: shrunk EPA/dropback vs the team's baseline."""
from . import config


def qb_values(qb_seasons, league_mean):
    """qb_seasons: list of qb_games lists, newest season first."""
    acc = {}
    for w, games in zip(config.QB_SEASON_WEIGHTS, qb_seasons):
        for q in games:
            a = acc.setdefault(q["qb"], {"db": 0.0, "epa": 0.0, "name": q.get("name"), "team": q["team"]})
            a["db"] += w * q["db"]
            a["epa"] += w * q["epa"]
    prior = league_mean + config.QB_REPLACEMENT
    n0 = config.QB_PRIOR_DB
    return {k: {"value": (a["epa"] + n0 * prior) / (a["db"] + n0), "db": a["db"], "name": a["name"], "team": a["team"]}
            for k, a in acc.items()}, prior


def team_baselines(qb_seasons, values):
    """Dropback-weighted QB value each team's ratings were built on
    (current season, plus half-weight last season)."""
    acc = {}
    for w, games in zip((1.0, 0.5), qb_seasons[:2]):
        for q in games:
            v = values.get(q["qb"])
            if not v:
                continue
            a = acc.setdefault(q["team"], [0.0, 0.0])
            a[0] += w * q["db"] * v["value"]
            a[1] += w * q["db"]
    return {t: s / n for t, (s, n) in acc.items() if n}


def adjustment(team, qb_id, values, baselines, replacement):
    """Points added to `team`'s margin for this starter vs its baseline."""
    if not qb_id or team not in baselines:
        return 0.0, None
    v = values.get(qb_id, {"value": replacement, "name": None})["value"]
    adj = (v - baselines[team]) * config.QB_DB_PER_GAME
    return max(-config.QB_MAX_ADJ, min(config.QB_MAX_ADJ, adj)), v
