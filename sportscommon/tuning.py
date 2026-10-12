"""Generic weekly re-weighting of model sources + home advantage.

Fits  actual_margin ~ sum(w_k * diff_k) + hfa * home_flag  on this season's
graded games (weights on the simplex, grid search; closed-form hfa), then sets

    live = (1 - alpha) * anchor + alpha * fit,   alpha = n / (n + prior_games)

where the anchor is the weights the season started with. (Stepping from last
week's weights instead would compound: alpha applied every week walks all the
way to the latest fit and lets noise swing the model.)
"""
from datetime import datetime, timezone
from itertools import product


def _grid(sources, step):
    steps = int(round(1 / step))
    k = len(sources)
    for combo in product(range(steps + 1), repeat=k - 1):
        if sum(combo) <= steps:
            vals = list(combo) + [steps - sum(combo)]
            yield {s: v * step for s, v in zip(sources, vals)}


def _pred(w, d):
    return sum(w[k] * d[k] for k in w)


def _fit_hfa(rows, w, lo, hi, default):
    num = den = 0.0
    for d, f, y in rows:
        num += (y - _pred(w, d)) * f
        den += f * f
    return max(lo, min(hi, num / den)) if den else default


def _mse(rows, w, h):
    return sum((y - _pred(w, d) - h * f) ** 2 for d, f, y in rows) / len(rows)


def season_anchor(doc, season, weights, hfa):
    """Season-start weights for shrinkage; set on the season's first tune."""
    a = doc.get("anchor")
    if a and a.get("season") == season:
        return a
    first = next((h for h in (doc.get("history") or []) if h.get("season") == season), None)
    base = first["before"] if first else {"weights": dict(weights), "home_field": hfa}
    a = {"season": season, "weights": dict(base["weights"]), "home_field": base["home_field"]}
    doc["anchor"] = a
    return a


def tune(rows, current_weights, current_hfa, min_games, prior_games, hfa_bounds, step=0.05, now=None, anchor=None):
    """rows: [(diffs {source: x}, home_flag, actual_margin)]. anchor: {'weights', 'home_field'}
    the season started with (defaults to current). Returns an entry dict."""
    sources = list(current_weights)
    base_w = {k: (anchor or {}).get("weights", current_weights).get(k, current_weights[k]) for k in sources}
    base_h = (anchor or {}).get("home_field", current_hfa)
    rows = [r for r in rows if all(k in r[0] for k in sources)]
    at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    before = {"weights": dict(current_weights), "home_field": current_hfa}
    entry = {"at": at, "games": len(rows), "before": before}
    if len(rows) < min_games:
        entry.update({"status": "insufficient", "after": before})
        return entry
    lo, hi = hfa_bounds
    best = None
    for w in _grid(sources, step):
        h = _fit_hfa(rows, w, lo, hi, current_hfa)
        m = _mse(rows, w, h)
        if best is None or m < best[0]:
            best = (m, w, h)
    _, fw, fh = best
    alpha = len(rows) / (len(rows) + prior_games)
    nw = {k: (1 - alpha) * base_w[k] + alpha * fw[k] for k in sources}
    tot = sum(nw.values())
    nw = {k: round(v / tot, 3) for k, v in nw.items()}
    nh = round((1 - alpha) * base_h + alpha * fh, 3)
    solo = {}
    for k in sources:
        w = {s: 1.0 if s == k else 0.0 for s in sources}
        solo[k] = {"rmse": round(_mse(rows, w, _fit_hfa(rows, w, lo, hi, current_hfa)) ** 0.5, 3)}
    entry.update({
        "status": "updated", "alpha": round(alpha, 3), "anchor": {"weights": base_w, "home_field": base_h},
        "fit": {"weights": {k: round(v, 3) for k, v in fw.items()}, "home_field": round(fh, 3)},
        "after": {"weights": nw, "home_field": nh},
        "rmse_before": round(_mse(rows, current_weights, current_hfa) ** 0.5, 3),
        "rmse_after": round(_mse(rows, nw, nh) ** 0.5, 3),
        "by_source": solo,
    })
    return entry
