"""Team ratings: opponent-adjusted points and efficiency (ridge), pace, Elo."""
import math
from datetime import date

from sportscommon import ridge

from . import config


def _done(games):
    return [g for g in games if g.get("completed") and g.get("home_score") is not None and g.get("away_score") is not None]


def _w(g, as_of):
    d = date.fromisoformat(g["date"]) if g.get("date") else as_of
    return config.DECAY_PER_DAY ** max(0, (as_of - d).days)


def game_poss(g):
    """Possessions in a game (average of both teams' estimates) or None without a box score."""
    t = ((g.get("box") or {}).get("team") or {})
    ph, pa = (t.get(g["home"]) or {}).get("poss"), (t.get(g["away"]) or {}).get("poss")
    if not ph or not pa:
        return None
    return (ph + pa) / 2


def _fit(obs, teams, prior):
    p_off = {k: v * config.PRIOR_KEEP for k, v in (prior or {}).get("off", {}).items()}
    p_def = {k: v * config.PRIOR_KEEP for k, v in (prior or {}).get("def", {}).items()}
    teams = set(teams) | set(p_off)
    if not teams:
        return None
    fit = ridge.fit(obs, teams, config.RIDGE_GAMES, p_off, p_def, fit_hfa=True, hfa_lam=50.0)
    if not obs and prior:
        fit["mu"], fit["hfa"] = prior["mu"], prior.get("hfa", 0.0)
    return fit


def fit_points(games, as_of, prior=None):
    """Points scored per game: y = mu + off[team] - def[opp] + hfa*home."""
    obs, teams = [], set()
    for g in _done(games):
        w = _w(g, as_of)
        h = 0 if g.get("neutral") else 1
        obs.append((g["home"], g["away"], h, g["home_score"], w))
        obs.append((g["away"], g["home"], 0, g["away_score"], w))
        teams |= {g["home"], g["away"]}
    return _fit(obs, teams, prior)


def fit_eff(games, as_of, prior=None):
    """Points per 100 possessions, opponent-adjusted (games with box scores only)."""
    obs, teams = [], set()
    for g in _done(games):
        poss = game_poss(g)
        if not poss:
            continue
        w = _w(g, as_of)
        h = 0 if g.get("neutral") else 1
        obs.append((g["home"], g["away"], h, 100 * g["home_score"] / poss, w))
        obs.append((g["away"], g["home"], 0, 100 * g["away_score"] / poss, w))
        teams |= {g["home"], g["away"]}
    return _fit(obs, teams, prior)


def fit_pace(games, as_of, prior=None):
    """{'mu': league possessions/game, 'team': {abbr: effect}}; game pace = mu + effect_h + effect_a."""
    rows = [(g, game_poss(g)) for g in _done(games)]
    rows = [(g, p) for g, p in rows if p]
    if not rows:
        return prior or {"mu": 99.0, "team": {}}
    tw = sum(_w(g, as_of) for g, _ in rows)
    mu = sum(_w(g, as_of) * p for g, p in rows) / tw
    acc = {}
    for g, p in rows:
        w = _w(g, as_of)
        for t in (g["home"], g["away"]):
            a = acc.setdefault(t, [0.0, 0.0])
            a[0] += w * (p - mu) / 2  # each team owns half of the deviation
            a[1] += w
    pt = (prior or {}).get("team", {})
    k = config.PACE_PRIOR_GAMES
    team = {t: (s + k * pt.get(t, 0.0) * config.PRIOR_KEEP) / (n + k) for t, (s, n) in acc.items()}
    for t, v in pt.items():
        team.setdefault(t, v * config.PRIOR_KEEP)
    return {"mu": mu, "team": team}


def net(fit, t):
    return (fit["off"].get(t, 0.0) + fit["def"].get(t, 0.0)) if fit else 0.0


def elo_run(games):
    """538-style Elo over completed games. Returns (final {team: elo}, pregame {game id: (home, away)})."""
    elo, season_of, pregame = {}, {}, {}
    hfa = config.HOME_COURT * config.ELO_PER_POINT
    for g in sorted(_done(games), key=lambda x: x.get("start") or ""):
        for t in (g["home"], g["away"]):
            if t not in elo:
                elo[t] = 1500.0
            elif season_of.get(t) != g["season"]:
                elo[t] = 1505 + (elo[t] - 1505) * (1 - config.ELO_REVERT)
            season_of[t] = g["season"]
        h, a = elo[g["home"]], elo[g["away"]]
        pregame[g["id"]] = (h, a)
        diff = h - a + (0 if g.get("neutral") else hfa)
        exp = 1 / (1 + 10 ** (-diff / 400))
        m = g["home_score"] - g["away_score"]
        if m == 0:
            continue
        fav_diff = diff if m > 0 else -diff
        mult = ((abs(m) + 3) ** 0.8) / (7.5 + 0.006 * fav_diff)
        shift = config.ELO_K * mult * ((1.0 if m > 0 else 0.0) - exp)
        elo[g["home"]] = h + shift
        elo[g["away"]] = a - shift
    return elo, pregame


def normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
