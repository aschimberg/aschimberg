"""Discrete NFL margin distribution with key-number (3, 7, ...) weighting."""
import math

from . import config

MAXM = 60


def key_weights(games):
    """Empirical / smooth frequency of each absolute final margin."""
    margins = [abs(int(g["home_pts"] - g["away_pts"])) for g in games
               if g["completed"] and g["season"] >= 2005 and g["home_pts"] is not None]
    if len(margins) < 500:
        return {}
    n = len(margins)
    sd = (sum(m * m for m in margins) / n) ** 0.5
    w = {}
    for k in range(0, MAXM + 1):
        emp = sum(1 for m in margins if m == k) / n
        smooth = (1 if k == 0 else 2) * math.exp(-0.5 * (k / sd) ** 2) / (sd * math.sqrt(2 * math.pi))
        w[k] = max(0.05, min(3.5, emp / smooth)) if smooth > 0 else 1.0
    return w


def _raw(loc, weights, sd):
    p = {m: math.exp(-0.5 * ((m - loc) / sd) ** 2) * weights.get(abs(m), 1.0) for m in range(-MAXM, MAXM + 1)}
    s = sum(p.values())
    return {m: v / s for m, v in p.items()}


def pmf(mu, weights, sd=None):
    """Key-number-weighted margin pmf whose mean is exactly mu.

    Reweighting toward 3/7/10 pulls the mean toward those numbers, so the
    normal's location is solved (bisection) to keep the model's projected
    margin as the distribution mean.
    """
    sd = sd or config.MARGIN_SD
    if not weights:
        return _raw(mu, weights, sd)
    lo, hi = mu - 6, mu + 6
    for _ in range(40):
        mid = (lo + hi) / 2
        mean = sum(m * v for m, v in _raw(mid, weights, sd).items())
        lo, hi = (mid, hi) if mean < mu else (lo, mid)
    return _raw((lo + hi) / 2, weights, sd)


def cover_probs(dist, home_spread):
    """(P home covers, P push, P away covers) for a home-perspective spread."""
    hc = sum(v for m, v in dist.items() if m + home_spread > 1e-9)
    push = sum(v for m, v in dist.items() if abs(m + home_spread) < 1e-9)
    return hc, push, 1 - hc - push


def market_mean(home_spread, weights, sd=None):
    """Mean margin at which the line is a coin flip (home and away equally likely
    to cover). With key numbers that isn't exactly -spread (at -8.5 the mean that
    splits it 50/50 sits nearer 9.3 than 8.5), so anchoring starts here."""
    key = (home_spread, sd, tuple(sorted((weights or {}).items())))
    if key not in _MARKET_MEAN:
        lo, hi = -home_spread - 8, -home_spread + 8
        for _ in range(24):
            mid = (lo + hi) / 2
            hc, _, ac = cover_probs(pmf(mid, weights, sd), home_spread)
            if hc < ac:
                lo = mid
            else:
                hi = mid
        _MARKET_MEAN[key] = (lo + hi) / 2
    return _MARKET_MEAN[key]


_MARKET_MEAN = {}


def win_prob(dist):
    return sum(v for m, v in dist.items() if m > 0) + 0.5 * dist.get(0, 0)
