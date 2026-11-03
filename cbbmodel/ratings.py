"""Opponent-adjusted efficiency and tempo (points per 100 possessions), KenPom-style but additive.

Each team gets offense, defense and tempo. A game's scoring rate for the home side is modeled as
    100 x pts / poss = off_home + def_away - avg + hca
(hca = half the home edge, per 100 possessions; zero at neutral sites), and its possessions as
    poss = tempo_home + tempo_away - avg_tempo.
Ratings start at a preseason prior worth PRIOR_GAMES games of evidence (last season's final rating
pulled toward average), so a team with two games played is still mostly its prior.
"""
from . import config

DEFAULT_POSS = 68.0


def _poss(g):
    if g.get("hp") and g.get("ap"):
        return (g["hp"] + g["ap"]) / 2
    return None


def league_avgs(games, priors=None):
    pts = poss = n = tp = 0.0
    for g in games:
        p = _poss(g)
        if p and g.get("hs") is not None:
            pts += g["hs"] + g["as"]
            poss += 2 * p
            tp += p
            n += 1
    if n:
        return 100 * pts / poss, tp / n
    if priors:
        vals = list(priors.values())
        return sum(v[0] for v in vals) / len(vals), sum(v[2] for v in vals) / len(vals)
    return 104.0, DEFAULT_POSS


def solve(games, d1, priors=None, prior_games=None, avgs=None):
    """Ratings {team: (off, def, tempo)} from finished games, shrunk toward priors."""
    k = config.PRIOR_GAMES if prior_games is None else prior_games
    games = [g for g in games if g.get("hs") is not None and g.get("as") is not None]
    avg, avg_t = avgs or league_avgs([g for g in games if g["h"] in d1 and g["a"] in d1], priors)
    hca = 100 * config.HOME_COURT / avg_t / 2
    teams = {t for g in games for t in (g["h"], g["a"])} | set(d1)
    prior = {}
    for t in teams:
        if priors and t in priors:
            prior[t] = priors[t]
        elif t in d1:
            prior[t] = (avg - config.NEW_TEAM_GAP, avg + config.NEW_TEAM_GAP, avg_t)
        else:
            prior[t] = (avg + config.NON_D1_OFF, avg + config.NON_D1_DEF, avg_t)
    off = {t: prior[t][0] for t in teams}
    dfn = {t: prior[t][1] for t in teams}
    tmp = {t: prior[t][2] for t in teams}
    rows = []
    for g in games:
        p = _poss(g)
        s = 0.0 if g.get("neutral") else hca
        rows.append((g["h"], g["a"], 100 * g["hs"] / (p or avg_t), 100 * g["as"] / (p or avg_t), p, s))
    by_team = {t: [] for t in teams}
    for r in rows:
        by_team[r[0]].append((r, True))
        by_team[r[1]].append((r, False))
    for _ in range(config.ITERATIONS):
        for t in teams:
            so = sd = st = 0.0
            nt = 0
            for (h, a, ph, pa, p, s), home in by_team[t]:
                if home:
                    so += ph - (dfn[a] - avg) - s
                    sd += pa - (off[a] - avg) + s
                    if p:
                        st += p - tmp[a] + avg_t
                        nt += 1
                else:
                    so += pa - (dfn[h] - avg) + s
                    sd += ph - (off[h] - avg) - s
                    if p:
                        st += p - tmp[h] + avg_t
                        nt += 1
            n = len(by_team[t])
            off[t] = (k * prior[t][0] + so) / (k + n) if k + n else prior[t][0]
            dfn[t] = (k * prior[t][1] + sd) / (k + n) if k + n else prior[t][1]
            tmp[t] = (k * prior[t][2] + st) / (k + nt) if k + nt else prior[t][2]
    return {"teams": {t: (off[t], dfn[t], tmp[t]) for t in teams}, "avg": avg, "avg_tempo": avg_t, "hca": hca,
            "games": {t: len(by_team[t]) for t in teams}}


def carry_over(r, d1):
    """Next season's priors: last season's ratings pulled toward the D1 average."""
    avg, avg_t = r["avg"], r["avg_tempo"]
    re, rt = config.REGRESS_EFF, config.REGRESS_TEMPO
    return {t: (avg + re * (o - avg), avg + re * (d - avg), avg_t + rt * (tp - avg_t))
            for t, (o, d, tp) in r["teams"].items() if t in d1}


def predict(r, home, away, neutral=False):
    """Projected (home points, away points, possessions)."""
    tm, avg, avg_t = r["teams"], r["avg"], r["avg_tempo"]
    if home not in tm or away not in tm:
        return None
    ho, hd, ht = tm[home]
    ao, ad, at = tm[away]
    s = 0.0 if neutral else r["hca"]
    poss = ht + at - avg_t
    return (ho + ad - avg + s) * poss / 100, (ao + hd - avg - s) * poss / 100, poss
