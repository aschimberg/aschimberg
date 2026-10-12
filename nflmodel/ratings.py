"""Opponent-adjusted EPA/play ratings (offense and defense), points scale."""
from sportscommon import ridge

from . import config


def fit_season(team_games, prior=None, decay=None, as_of_week=None):
    """team_games: aggregated offense lines for one season (completed games)."""
    decay = config.EPA_DECAY if decay is None else decay
    teams = {t["team"] for t in team_games} | {t["opp"] for t in team_games}
    if prior:
        teams |= set(prior["off"])
    if not teams:
        return None
    latest = as_of_week or max((t["week"] + (30 if t["season_type"] == "postseason" else 0) for t in team_games), default=0)
    obs = []
    for t in team_games:
        if t["n"] <= 0:
            continue
        wk = t["week"] + (30 if t["season_type"] == "postseason" else 0)
        w = t["n"] * decay ** max(0, latest - wk)
        obs.append((t["team"], t["opp"], t["home"], t["epa"] / t["n"], w))
    p_off = {k: v * config.PRIOR_KEEP for k, v in (prior or {}).get("off", {}).items()}
    p_def = {k: v * config.PRIOR_KEEP for k, v in (prior or {}).get("def", {}).items()}
    fit = ridge.fit(obs, teams, config.EPA_RIDGE, p_off, p_def, fit_hfa=True, hfa_lam=2000.0)
    games = {}
    for t in team_games:
        games[t["team"]] = games.get(t["team"], 0) + 1
    fit["games"] = games
    return fit


def team_net(fit, team):
    """Net EPA/play (offense + defense) above average."""
    if not fit:
        return 0.0
    return fit["off"].get(team, 0.0) + fit["def"].get(team, 0.0)
