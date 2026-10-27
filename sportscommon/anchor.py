"""Market-anchored pricing for spreads and totals (CFB, NFL, NBA).

On 2,800+ CFB, 1,000+ NFL and 1,100+ NBA games, the model's gap to the
betting line did not predict results: regressing (result - line) on
(model - line) gives slopes of -0.05 ± 0.08, -0.13 ± 0.15 and +0.02 ± 0.13,
and 6-10 point gaps covered less often than small ones. Pricing bets
straight off the model's projection made them look like 56-58% covers when
they hit 47-51%.

So a bet is priced at a fair number that moves only part of the way from the
line toward the model (weight), and never more than max_shift points. The
pick's side still follows the model; its cover chance, EV and tier come from
the fair number.
"""
from .odds import american_to_decimal

EV_LEAN, EV_PLAY, EV_STRONG = 0.0, 0.015, 0.03  # EV per unit at the posted (or -110) price


def fair(model, market, weight, max_shift):
    shift = weight * (model - market)
    return market + max(-max_shift, min(max_shift, shift))


def ev(p_win, odds=None, p_push=0.0):
    """Profit per unit staked; a push returns the stake."""
    return p_win * (american_to_decimal(odds or -110) - 1) - (1 - p_win - p_push)


def tier(ev_value):
    if ev_value is None:
        return "pass"
    if ev_value >= EV_STRONG:
        return "strong"
    if ev_value >= EV_PLAY:
        return "play"
    if ev_value > EV_LEAN:
        return "lean"
    return "pass"
