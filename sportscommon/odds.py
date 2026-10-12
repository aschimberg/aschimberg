"""Odds conversions and margin distributions."""
import math


def american_to_decimal(a):
    a = float(a)
    return 1 + (a / 100 if a > 0 else 100 / -a)


def american_to_prob(a):
    return 1 / american_to_decimal(a)


def prob_to_american(p):
    if p <= 0 or p >= 1:
        return None
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def no_vig(p1, p2):
    s = p1 + p2
    return p1 / s, p2 / s


def ev(prob, american):
    """Expected profit per 1 unit staked."""
    return prob * (american_to_decimal(american) - 1) - (1 - prob)


def fmt_american(a):
    if a is None:
        return "—"
    a = int(round(a))
    return f"+{a}" if a > 0 else str(a).replace("-", "−")


def normal_cdf(x, mu=0.0, sd=1.0):
    return 0.5 * (1 + math.erf((x - mu) / (sd * math.sqrt(2))))


def poisson_pmf(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)
