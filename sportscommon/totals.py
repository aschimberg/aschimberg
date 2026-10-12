"""Over/under picks: the model's projected total vs the market total.

Shared by CFB and NFL (NHL prices totals from its score simulation and only
uses grade() here). Edge = |model total - market total| in points; the side is
the direction of the gap. Probability uses a normal approximation of the
final total around the model total (pushes count half).
"""
import math


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


def make_pick(model_total, line, sd, lean, play, strong, over_odds=None, under_odds=None):
    """{'side','line','model','edge','tier','prob','play','odds'} or None without both numbers."""
    if model_total is None or line is None:
        return None
    diff = model_total - line
    side = "over" if diff >= 0 else "under"
    edge = abs(diff)
    p_over = 1 - _cdf((line - model_total) / sd)
    t = tier(edge, lean, play, strong)
    return {"side": side, "line": line, "model": round(model_total, 1), "edge": round(edge, 1), "tier": t,
            "prob": round(p_over if side == "over" else 1 - p_over, 3), "play": t in ("play", "strong"),
            "odds": (over_odds if side == "over" else under_odds)}


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
        out.append(f"{t['side'].title()} {t['line']:g} ({matchup(c)}, {kickoff(c, tz)}) — model {t['model']:g}, edge {t['edge']:g}")
    return out
