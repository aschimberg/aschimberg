"""Pick confidence (0-100), separate from edge.

Edge says how far the blended model is from the line; confidence says how
much to trust the side. A small edge can still be high confidence when every
rating source independently lands on the same side, the market is moving that
way, and situational trends agree.
"""
from statistics import pstdev

from . import config
from .model import SOURCES


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def label(score):
    if score >= config.CONF_HIGH:
        return "High"
    if score >= config.CONF_MEDIUM:
        return "Medium"
    return "Low"


def score(pick, line_home, diffs, hfa_flag, line_move, trends, split_info, data_quality):
    """Return {"score", "label", "components", "sources"} or None without a line.

    pick: model.make_pick output (needs ats_side, edge)
    line_home: market spread from the home team's perspective
    diffs: model.source_diffs output; hfa_flag: model.home_flag
    line_move: current minus opening home spread
    trends: {"home": [...], "away": [...]} from ats.relevant_trends
    split_info: splits.analyze output or None
    data_quality: 0..1
    """
    if not pick.get("ats_side") or line_home is None:
        return None
    side = pick["ats_side"]
    sign = 1 if side == "home" else -1
    line = line_home

    # Each source's projected margin and expected cover margin for the pick side.
    margins = {k: diffs[k] + config.HOME_FIELD * hfa_flag for k in SOURCES if k in diffs}
    covers = {k: sign * (m + line) for k, m in margins.items()}
    wsum = sum(config.WEIGHTS[k] for k in covers) or 1
    agreement = sum(config.WEIGHTS[k] for k, c in covers.items() if c > 0) / wsum
    spread_sd = pstdev(margins.values()) if len(margins) > 1 else 0.0
    consistency = _clamp(1 - spread_sd / 10)

    edge = _clamp((pick.get("edge") or 0) / config.STRONG_EDGE)

    # Market: line moving toward our side (money agreeing) and sharp flags.
    market = 0.5
    if line_move:
        market += _clamp(-sign * line_move / 4, -0.5, 0.5)
    for f in (split_info or {}).get("flags", []):
        if f["market"] != "spread" or f["type"] not in ("sharp", "rlm"):
            continue
        market += 0.25 if f["side"] == side else -0.25
    market = _clamp(market)

    # Trends: notable situational ATS trends for our team or against the opponent.
    other = "away" if side == "home" else "home"
    net = 0
    for t in trends.get(side, []):
        if t.get("notable"):
            net += 1 if t["pct"] >= 0.5 else -1
    for t in trends.get(other, []):
        if t.get("notable"):
            net += 1 if t["pct"] < 0.5 else -1
    trend_c = _clamp(0.5 + net * 0.125)

    comps = {
        "agreement": round(agreement, 3),
        "consistency": round(consistency, 3),
        "edge": round(edge, 3),
        "market": round(market, 3),
        "trends": round(trend_c, 3),
        "data": round(_clamp(data_quality), 3),
    }
    total = round(100 * sum(config.CONF_WEIGHTS[k] * v for k, v in comps.items()))
    return {
        "score": total,
        "label": label(total),
        "components": comps,
        "sources": {k: round(m, 1) for k, m in margins.items()},
        "agree": sum(1 for c in covers.values() if c > 0),
        "n_sources": len(covers),
    }


def data_quality(game, ratings):
    """1.0 when both teams have current SP+, FPI and Elo; lower otherwise."""
    q = []
    for side in ("home", "away"):
        r = ratings.get(game[side])
        if r is None:
            q.append(0.3)
        elif r.get("sources") == ["prior"]:
            q.append(0.6)
        else:
            q.append(len(r.get("sources", [])) / len(SOURCES))
    return sum(q) / len(q)
