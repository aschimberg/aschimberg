"""Final-score distribution from regulation scoring rates.

1. Regulation (non-empty-net) goals ~ independent Poisson(lh), Poisson(la).
2. Empty-net goals: a team leading by 1 adds one with p=EN1; by 2 with p=EN2.
3. Ties go to OT: 3-on-3 decides OT_DECIDED of them in proportion to scoring
   rates; the rest are shootouts (~coin flip). The winner gets +1 goal.
"""
from sportscommon.odds import poisson_pmf

from . import config


def distribution(lh, la):
    lh, la = max(lh, 0.3), max(la, 0.3)
    n = config.MAX_GOALS
    ph = [poisson_pmf(k, lh) for k in range(n + 1)]
    pa = [poisson_pmf(k, la) for k in range(n + 1)]
    p_ot_home = config.OT_DECIDED * lh / (lh + la) + (1 - config.OT_DECIDED) * 0.5
    final = {}

    def add(h, a, p):
        final[(h, a)] = final.get((h, a), 0.0) + p

    for h in range(n + 1):
        for a in range(n + 1):
            p = ph[h] * pa[a]
            m = h - a
            if m == 0:
                add(h + 1, a, p * p_ot_home)
                add(h, a + 1, p * (1 - p_ot_home))
            elif abs(m) in (1, 2):
                e = config.EN_GOAL_LEAD1 if abs(m) == 1 else config.EN_GOAL_LEAD2
                if m > 0:
                    add(h + 1, a, p * e)
                else:
                    add(h, a + 1, p * e)
                add(h, a, p * (1 - e))
            else:
                add(h, a, p)
    return final


def markets(dist, total_line=None):
    win = sum(p for (h, a), p in dist.items() if h > a)
    home_m15 = sum(p for (h, a), p in dist.items() if h - a >= 2)
    away_m15 = sum(p for (h, a), p in dist.items() if a - h >= 2)
    exp_h = sum(h * p for (h, a), p in dist.items())
    exp_a = sum(a * p for (h, a), p in dist.items())
    out = {"home_win": win, "away_win": 1 - win, "home_m15": home_m15, "away_p15": 1 - home_m15,
           "away_m15": away_m15, "home_p15": 1 - away_m15, "exp_home": exp_h, "exp_away": exp_a,
           "reg_tie": None}
    if total_line is not None:
        out["over"] = sum(p for (h, a), p in dist.items() if h + a > total_line)
        out["under"] = sum(p for (h, a), p in dist.items() if h + a < total_line)
    return out
