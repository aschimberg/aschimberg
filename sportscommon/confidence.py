"""Generic pick confidence (0-100), separate from edge.

Same idea as the CFB model: how many independent sources land on the pick's
side and how tightly they agree, the edge itself, market movement, situational
trends and data quality.
"""
from statistics import pstdev

DEFAULT_WEIGHTS = {"agreement": 0.25, "consistency": 0.10, "edge": 0.25, "market": 0.15, "trends": 0.15, "data": 0.10}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def label(score, high=70, medium=55):
    return "High" if score >= high else "Medium" if score >= medium else "Low"


def score(side, covers, weights, edge_frac, consistency_scale, market=0.5, trends=0.5, data=1.0,
          conf_weights=None, high=70, medium=55, extra=None):
    """covers: {source: expected cover margin for `side`} (>0 = source agrees).
    weights: {source: blend weight}. edge_frac: edge relative to 'strong' (0..1+).
    consistency_scale: source spread (same units as covers) at which consistency hits 0.
    """
    cw = conf_weights or DEFAULT_WEIGHTS
    wsum = sum(weights.get(k, 0) for k in covers) or 1
    agreement = sum(weights.get(k, 0) for k, c in covers.items() if c > 0) / wsum
    spread = pstdev(covers.values()) if len(covers) > 1 else 0.0
    comps = {
        "agreement": round(agreement, 3),
        "consistency": round(_clamp(1 - spread / consistency_scale), 3),
        "edge": round(_clamp(edge_frac), 3),
        "market": round(_clamp(market), 3),
        "trends": round(_clamp(trends), 3),
        "data": round(_clamp(data), 3),
    }
    total = round(100 * sum(cw[k] * v for k, v in comps.items()))
    out = {"score": total, "label": label(total, high, medium), "components": comps,
           "agree": sum(1 for c in covers.values() if c > 0), "n_sources": len(covers), "side": side}
    if extra:
        out.update(extra)
    return out


def trend_component(trends, side, other):
    net = 0
    for t in trends.get(side, []):
        if t.get("notable"):
            net += 1 if t["pct"] >= 0.5 else -1
    for t in trends.get(other, []):
        if t.get("notable"):
            net += 1 if t["pct"] < 0.5 else -1
    return _clamp(0.5 + net * 0.125)


def market_component(sign, line_move, per=4.0):
    """sign: +1 if pick is home, -1 if away. line_move: current - open (home spread)."""
    if not line_move:
        return 0.5
    return _clamp(0.5 + _clamp(-sign * line_move / per, -0.5, 0.5))
