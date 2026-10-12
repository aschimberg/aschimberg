"""538-style NFL Elo from game results."""
import math

from . import config

BASE = 1505.0


def run(games):
    """Walk completed games in date order. Returns (ratings, pregame) where
    pregame maps game id -> (home_elo, away_elo) before that game."""
    elo, last_season, pregame = {}, {}, {}
    hfa = config.HOME_FIELD * config.ELO_PER_POINT
    ordered = sorted((g for g in games if g["season"] >= config.ELO_START_SEASON), key=lambda g: (g["start"] or "", g["id"]))
    for g in ordered:
        for t in (g["home"], g["away"]):
            if t not in elo:
                elo[t] = BASE
            elif last_season.get(t) != g["season"]:
                elo[t] = BASE + (elo[t] - BASE) * (1 - config.ELO_REVERT)
            last_season[t] = g["season"]
        h, a = elo[g["home"]], elo[g["away"]]
        pregame[g["id"]] = (h, a)
        if not g["completed"]:
            continue
        diff = h - a + (0 if g["neutral"] else hfa)
        exp_home = 1 / (1 + 10 ** (-diff / 400))
        margin = g["home_pts"] - g["away_pts"]
        actual = 1.0 if margin > 0 else 0.0 if margin < 0 else 0.5
        winner_diff = diff if margin > 0 else -diff
        mult = math.log(abs(margin) + 1) * 2.2 / (winner_diff * 0.001 + 2.2) if margin else 1.0
        shift = config.ELO_K * mult * (actual - exp_home)
        elo[g["home"]] = h + shift
        elo[g["away"]] = a - shift
    return elo, pregame


def current(elo, team, season, last_played_season):
    """Rating for a team's next game, applying the off-season reversion if due."""
    r = elo.get(team, BASE)
    if last_played_season is not None and last_played_season < season:
        r = BASE + (r - BASE) * (1 - config.ELO_REVERT)
    return r
