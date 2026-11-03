"""Possession-by-possession basketball simulation (NBA and college).

Every possession is played out: turnover, shooting foul (two free throws), three or two, and on a
miss an offensive rebound that keeps the possession alive. The clock runs per possession, so the
end of the game behaves like basketball: a trailing team fouls in the last minute, a team down
three looks for a three, a leading team milks the shot clock, and ties go to five-minute overtimes.

Shooting is scaled per team so each side's expected points match the model's projection (the
"target"); team style (three-point rate, turnovers, offensive rebounding, free throws) shapes the
spread of outcomes around that average.
"""
import random

from . import parallel

# score_effect: share of scoring rate a team gives back per point of lead (leaders ease off and
# play the bench, trailers press), which is why real margins spread less than independent
# possessions would. Tuned so simulated margins match how far real games land from the line.
LEAGUES = {
    "nba": {"secs": 2880, "ot": 300, "shot_clock": 24, "pace_sd": 0.015, "foul_lead": 9, "score_effect": 0.0015,
            "style": {"tov": 0.135, "foul": 0.105, "three": 0.40, "p3": 0.360, "p2": 0.545, "ft": 0.785, "orb": 0.245}},
    "cbb": {"secs": 2400, "ot": 300, "shot_clock": 30, "pace_sd": 0.06, "foul_lead": 8, "score_effect": 0.003,
            "style": {"tov": 0.170, "foul": 0.100, "three": 0.385, "p3": 0.340, "p2": 0.510, "ft": 0.710, "orb": 0.290}},
}


def _rates(style, k):
    p3 = min(0.95, style["p3"] * k)
    p2 = min(0.95, style["p2"] * k)
    return p3, p2


def expected_ppp(style, k=1.0):
    """Expected points per possession for a shooting scale k."""
    p3, p2 = _rates(style, k)
    f, th = style["foul"], style["three"]
    made = f * 2 * style["ft"] + (1 - f) * (th * 3 * p3 + (1 - th) * 2 * p2)
    miss = (1 - f) * (th * (1 - p3) + (1 - th) * (1 - p2))
    return (1 - style["tov"]) * made / (1 - miss * style["orb"])


def scale_for(style, ppp):
    """Shooting scale that makes a style produce the target points per possession."""
    lo, hi = 0.2, 3.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if expected_ppp(style, mid) < ppp:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


LEAD_CAP = 20


def _team(style, ppp, effect=0.0):
    """Possession tuples by the offense's lead (-LEAD_CAP..LEAD_CAP)."""
    out = []
    for lead in range(-LEAD_CAP, LEAD_CAP + 1):
        k = scale_for(style, max(0.3, ppp * (1 - effect * lead)))
        p3, p2 = _rates(style, k)
        out.append((style["tov"], style["foul"], style["three"], p3, p2, style["ft"], style["orb"]))
    return out


def _possession(rng, t, need3=False):
    """Points from one possession."""
    tov, foul, three, p3, p2, ft, orb = t
    rand = rng.random
    if rand() < tov:
        return 0
    while True:
        if not need3 and rand() < foul:
            return (rand() < ft) + (rand() < ft)
        if need3 or rand() < three:
            if rand() < p3:
                return 3
        elif rand() < p2:
            return 2
        if rand() >= orb:
            return 0


def play(rng, lg, home, away, poss):
    """One game. home/away are prepared team tuples; poss is each team's regulation possessions."""
    pace = poss * max(0.75, 1 + rng.gauss(0, lg["pace_sd"]))
    mean = lg["secs"] / (2 * pace)
    clock = lg["secs"]
    score = [0, 0]
    teams = (home, away)
    off = 0 if rng.random() < 0.5 else 1
    ot = False
    lead_foul, sc = lg["foul_lead"], lg["shot_clock"]
    rand = rng.random
    while True:
        while clock > 0:
            d = 1 - off
            margin = score[off] - score[d]  # offense's lead
            t = teams[off][LEAD_CAP + max(-LEAD_CAP, min(LEAD_CAP, margin))]
            if (clock < 40 and 3 <= margin <= lead_foul) or (clock < 15 and 0 < margin < 3):
                # trailing defense fouls right away: two free throws for the leader
                score[off] += (rand() < t[5]) + (rand() < t[5])
                clock -= 3 + 4 * rand()
            elif clock < 35 and margin > 0:
                # leader runs the shot clock down
                score[off] += _possession(rng, t)
                clock -= min(clock, sc - 2 * rand())
            else:
                score[off] += _possession(rng, t, clock < 25 and margin == -3)
                clock -= mean * (0.35 + 1.3 * rand())
            off = d
        if score[0] != score[1]:
            return score[0], score[1], ot
        ot = True
        clock = lg["ot"]


PILOT = 4000


def _games(lg, n, rng, prep, home_pts, away_pts, poss, draws):
    margins, totals, ots, extra = [], [], [], []
    base_h, base_a = prep(home_pts, "h"), prep(away_pts, "a")
    for _ in range(n):
        if draws:
            dh, da, info = draws(rng)
            th, ta = prep(home_pts + dh, "h"), prep(away_pts + da, "a")
            extra.append(info)
        else:
            th, ta = base_h, base_a
        h, a, ot = play(rng, lg, th, ta, poss)
        margins.append(h - a)
        totals.append(h + a)
        ots.append(ot)
    return margins, totals, ots, extra


def run(league, home_pts, away_pts, poss, n, seed=0, home_style=None, away_style=None, draws=None, calibrate=True):
    """Simulate n games. draws(rng) -> (home_adj, away_adj, info) shifts each team's target per game
    (e.g. a lineup draw). A short pilot run first measures how far end-of-game fouling and overtime
    move the averages, and the targets are nudged so the full run's means land on the projection."""
    lg = LEAGUES[league]
    styles = {"h": home_style or lg["style"], "a": away_style or lg["style"]}
    cache = {}

    def prep(pts, key):
        pts = round(pts * 4) / 4
        c = cache.get((key, pts))
        if c is None:
            c = cache[(key, pts)] = _team(styles[key], max(0.4, pts / poss), lg["score_effect"])
        return c

    hp, ap = home_pts, away_pts
    if calibrate:
        exp_m = home_pts - away_pts + (_mean_adj(draws, seed, n) if draws else 0.0)
        exp_t = home_pts + away_pts + (_mean_adj(draws, seed, n, total=True) if draws else 0.0)
        pilot = min(PILOT, max(1500, n // 4))
        for i in range(3):  # score effects shrink any nudge a little, so nudge a few times
            m, t, _, _ = _games(lg, pilot, random.Random(seed + 7 + i), prep, hp, ap, poss, draws)
            bm, bt = sum(m) / pilot - exp_m, sum(t) / pilot - exp_t
            hp, ap = hp - (bt + bm * 1.15) / 2, ap - (bt - bm * 1.15) / 2
    if draws:  # lineup draws are closures; keep those runs in one process
        margins, totals, ots, extra = _games(lg, n, random.Random(seed), prep, hp, ap, poss, None if not draws else draws)
        return {"margins": margins, "totals": totals, "ot": ots, "played": extra}
    parts = parallel.chunks(_chunk, n, (league, seed, hp, ap, poss, home_style, away_style))
    return {"margins": [x for p in parts for x in p[0]], "totals": [x for p in parts for x in p[1]],
            "ot": [x for p in parts for x in p[2]], "played": []}


def _chunk(league, seed, hp, ap, poss, home_style, away_style, n, i):
    lg = LEAGUES[league]
    styles = {"h": home_style or lg["style"], "a": away_style or lg["style"]}
    th = _team(styles["h"], max(0.4, (round(hp * 4) / 4) / poss), lg["score_effect"])
    ta = _team(styles["a"], max(0.4, (round(ap * 4) / 4) / poss), lg["score_effect"])
    rng = random.Random(seed if not i else seed * 31 + i)
    m, t, o = [], [], []
    for _ in range(n):
        h, a, ot = play(rng, lg, th, ta, poss)
        m.append(h - a)
        t.append(h + a)
        o.append(ot)
    return m, t, o


def _mean_adj(draws, seed, n, total=False):
    k = min(PILOT, max(1500, n // 4))
    rng = random.Random(seed + 11)
    vals = [draws(rng) for _ in range(k)]
    return sum((v[0] + v[1]) if total else (v[0] - v[1]) for v in vals) / k
