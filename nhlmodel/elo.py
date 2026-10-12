"""NHL Elo from final scores (cached games)."""
import math

from . import config

BASE = 1500.0


def run(games):
    elo, season_of = {}, {}
    hfa = config.HOME_ICE * config.ELO_PER_GOAL
    for g in sorted(games, key=lambda x: x.get("start") or ""):
        for t in (g["home"], g["away"]):
            if t not in elo:
                elo[t] = BASE
            elif season_of.get(t) != g["season"]:
                elo[t] = BASE + (elo[t] - BASE) * (1 - config.ELO_REVERT)
            season_of[t] = g["season"]
        h, a = elo[g["home"]], elo[g["away"]]
        diff = h - a + hfa
        exp = 1 / (1 + 10 ** (-diff / 400))
        m = (g["home_score"] or 0) - (g["away_score"] or 0)
        if m == 0:
            continue
        actual = 1.0 if m > 0 else 0.0
        mult = math.log(abs(m) + 1)
        shift = config.ELO_K * mult * (actual - exp)
        elo[g["home"]] = h + shift
        elo[g["away"]] = a - shift
    return elo, season_of
