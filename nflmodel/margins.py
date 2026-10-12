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


def pmf(mu, weights, sd=None):
    sd = sd or config.MARGIN_SD
    p = {}
    for m in range(-MAXM, MAXM + 1):
        p[m] = math.exp(-0.5 * ((m - mu) / sd) ** 2) * weights.get(abs(m), 1.0)
    s = sum(p.values())
    return {m: v / s for m, v in p.items()}


def cover_probs(dist, home_spread):
    """(P home covers, P push, P away covers) for a home-perspective spread."""
    hc = sum(v for m, v in dist.items() if m + home_spread > 1e-9)
    push = sum(v for m, v in dist.items() if abs(m + home_spread) < 1e-9)
    return hc, push, 1 - hc - push


def win_prob(dist):
    return sum(v for m, v in dist.items() if m > 0) + 0.5 * dist.get(0, 0)
