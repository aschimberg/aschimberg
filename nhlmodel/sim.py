"""Final-score distribution from regulation scoring rates.

1. Regulation (non-empty-net) goals ~ independent Poisson(lh), Poisson(la),
   reweighted by margin (MARGIN_WEIGHTS) to match real tie and blowout rates.
2. Empty-net goals: the leader adds 0/1/2 by its lead (EN_GOALS).
3. Ties go to OT: 3-on-3 decides OT_DECIDED of them in proportion to scoring
   rates; the rest are shootouts (~coin flip). The winner gets +1 goal.
"""
from sportscommon.odds import poisson_pmf

from . import config


def _regulation(lh, la):
    n = config.MAX_GOALS
    ph = [poisson_pmf(k, lh) for k in range(n + 1)]
    pa = [poisson_pmf(k, la) for k in range(n + 1)]
    last = len(config.MARGIN_WEIGHTS) - 1
    cells = [(h, a, ph[h] * pa[a] * config.MARGIN_WEIGHTS[min(abs(h - a), last)]) for h in range(n + 1) for a in range(n + 1)]
    norm = sum(p for _, _, p in cells)
    return [(h, a, p / norm) for h, a, p in cells]


def distribution(lh, la):
    lh, la = max(lh, 0.3), max(la, 0.3)
    # the margin reweighting favors low-scoring ties; rescale the rates so expected
    # regulation goals still equal lh + la
    scale = 1.0
    for _ in range(3):
        cells = _regulation(lh * scale, la * scale)
        scale *= (lh + la) / sum((h + a) * p for h, a, p in cells)
    p_ot_home = config.OT_DECIDED * lh / (lh + la) + (1 - config.OT_DECIDED) * 0.5
    final = {}

    def add(h, a, p):
        final[(h, a)] = final.get((h, a), 0.0) + p

    for h, a, p in cells:
        m = h - a
        if m == 0:
            add(h + 1, a, p * p_ot_home)
            add(h, a + 1, p * (1 - p_ot_home))
            continue
        en = config.EN_GOALS.get(min(abs(m), max(config.EN_GOALS)), (1.0,))
        for k, pk in enumerate(en):
            if m > 0:
                add(h + k, a, p * pk)
            else:
                add(h, a + k, p * pk)
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
