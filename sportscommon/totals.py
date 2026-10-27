"""Over/under picks: the model's projected total vs the market total.

Shared by CFB and NFL (NHL prices totals from its score simulation and only
uses grade() here). Edge = |model total - market total| in points; the side is
the direction of the gap. Probability uses a normal approximation of the
final total. With `anchor=(weight, max_shift)` it is centered on the
market-anchored fair total (see anchor.py) and the tier comes from EV;
without it, on the model total with tiers by edge.
"""
import math

from . import anchor as anc


def _cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def tier(edge, lean, play, strong):
    if edge >= strong:
        return "strong"
    if edge >= play:
        return "play"
    if edge >= lean:
        return "lean"
    return "pass"


def make_pick(model_total, line, sd, lean, play, strong, over_odds=None, under_odds=None, anchor=None):
    """{'side','line','model','edge','tier','prob','play','odds'} (+ 'fair','ev' when anchored) or None."""
    if model_total is None or line is None:
        return None
    diff = model_total - line
    side = "over" if diff >= 0 else "under"
    edge = abs(diff)
    center = anc.fair(model_total, line, *anchor) if anchor else model_total
    p_over = 1 - _cdf((line - center) / sd)
    p = p_over if side == "over" else 1 - p_over
    odds = over_odds if side == "over" else under_odds
    out = {"side": side, "line": line, "model": round(model_total, 1), "edge": round(edge, 1), "prob": round(p, 3), "odds": odds}
    if anchor:
        ev = anc.ev(p, odds)
        out.update({"fair": round(center, 1), "ev": round(ev, 4), "tier": anc.tier(ev)})
    else:
        out["tier"] = tier(edge, lean, play, strong)
    out["play"] = out["tier"] in ("play", "strong")
    return out


def grade(tp, total_pts):
    """'W'/'L'/'P' for a total pick given the final combined score."""
    if not tp or total_pts is None:
        return None
    if total_pts == tp["line"]:
        return "P"
    return "W" if (total_pts > tp["line"]) == (tp["side"] == "over") else "L"


def briefing_lines(games, matchup, kickoff, tz, limit=5):
    """'Totals' section for an ntfy briefing: play-level over/unders, biggest edge first."""
    plays = sorted((c for c in games if ((c.get("pick") or {}).get("total_pick") or {}).get("play")),
                   key=lambda c: -(c["pick"]["total_pick"].get("edge") or 0))
    if not plays:
        return []
    out = ["", "**🔢 Totals**"]
    for c in plays[:limit]:
        t = c["pick"]["total_pick"]
        ev = f", EV {t['ev'] * 100:+.1f}%" if t.get("ev") is not None else ""
        out.append(f"{t['side'].title()} {t['line']:g} ({matchup(c)}, {kickoff(c, tz)}) — model {t['model']:g}, edge {t['edge']:g}{ev}")
    return out
