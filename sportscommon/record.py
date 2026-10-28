"""Season records from a pick log (SU, ATS/ML, totals, by tier, by confidence, by week)."""
from collections import defaultdict

from .odds import american_to_decimal


def rec(items, key):
    r = {"W": 0, "L": 0, "P": 0}
    for p in items:
        v = (p.get("result") or {}).get(key)
        if v:
            r[v] += 1
    n = r["W"] + r["L"]
    r["pct"] = round(r["W"] / n, 3) if n else None
    return r


TIERS = {"lean": ("lean", "play", "strong"), "play": ("play", "strong"), "strong": ("strong",)}
TOTAL_TIERS = TIERS


def qualifies(p, min_edge, name):
    """Counts toward the lean+/play+/strong record: the pick's own tier when it has
    one (tiers come from EV since market anchoring), else its point edge (old logs)."""
    t = p.get("tier")
    if t in ("pass", "lean", "play", "strong"):
        return t in TIERS[name]
    return (p.get("edge") or 0) >= min_edge


PLAY_TIERS = ("play", "strong")


def _units(res, odds):
    if res == "P":
        return 0.0
    return round(american_to_decimal(odds or -110) - 1, 3) if res == "W" else -1.0


def _part():
    return {"W": 0, "L": 0, "P": 0, "units": 0.0}


def _finish(r):
    n = r["W"] + r["L"]
    r["pct"] = round(r["W"] / n, 3) if n else None
    r["units"] = round(r["units"], 2)
    risked = n + r["P"]
    r["roi"] = round(r["units"] / risked, 3) if risked else None
    return r


def plays(items):
    """Every pick rated play or strong, by the tier it was logged with: sides
    (spread or moneyline) and totals separately, plus both together. Units use
    the logged payout, else the pick's price, else -110."""
    side_r, total_r = _part(), _part()
    for p in items:
        res = p.get("result") or {}
        side = res.get("ats")
        if p.get("tier") in PLAY_TIERS and side in ("W", "L", "P"):
            side_r[side] += 1
            side_r["units"] += res["units"] if res.get("units") is not None else _units(side, p.get("odds"))
        t = p.get("total_pick") or {}
        tot = res.get("total")
        if t.get("tier") in PLAY_TIERS and tot in ("W", "L", "P"):
            total_r[tot] += 1
            total_r["units"] += res["total_units"] if res.get("total_units") is not None else _units(tot, t.get("odds"))
    r = {k: side_r[k] + total_r[k] for k in ("W", "L", "P", "units")}
    _finish(side_r)
    _finish(total_r)
    r["sides"] = side_r["W"] + side_r["L"] + side_r["P"]
    r["totals"] = total_r["W"] + total_r["L"] + total_r["P"]
    r["side"], r["total"] = side_r, total_r
    return _finish(r)


def _wl(r):
    return f"{r['W']}–{r['L']}" + (f"–{r['P']}" if r["P"] else "")


def plays_sub(r, side_label="ATS"):
    """'ATS 20–17 · O/U 6–7 · -0.4u' for a plays record box."""
    if not r or not (r["W"] + r["L"] + r["P"]):
        return "play or strong picks · none graded yet"
    return f"{side_label} {_wl(r['side'])} · O/U {_wl(r['total'])} · {r['units']:+.1f}u"


def totals(items, edges=None):
    """Over/under record: every graded total pick, then by the pick's own tier (lean+ / play+ / strong)."""
    xs = [p for p in items if (p.get("result") or {}).get("total") and p.get("total_pick")]
    out = {"all": rec(xs, "total")}
    for name, tiers in TOTAL_TIERS.items():
        out[name] = rec([p for p in xs if p["total_pick"].get("tier") in tiers], "total")
    if edges:
        out["edges"] = dict(zip(("lean", "play", "strong"), edges))
    return out


def compute(picks, season, lean, play, strong, period_key=("season", "season_type", "week"), total_edges=None):
    graded = [p for p in picks.values() if p.get("result") and (season is None or p["season"] == season)]
    ats = [p for p in graded if p["result"].get("ats")]

    def tier(min_edge, name):
        return rec([p for p in ats if qualifies(p, min_edge, name)], "ats")

    weeks = defaultdict(list)
    for p in graded:
        weeks[tuple(p.get(k) for k in period_key)].append(p)
    by_week = [
        {"season": k[0], "season_type": k[1], "week": k[2], "su": rec(items, "su"), "ats": rec(items, "ats"),
         "ats_plays": rec([p for p in items if qualifies(p, play, "play")], "ats"), "ou": rec(items, "total")}
        for k, items in sorted(weeks.items(), key=lambda kv: (kv[0][0], kv[0][1] != "regular", kv[0][2]))
    ]
    return {
        "su": rec(graded, "su"), "ats": rec(ats, "ats"),
        "ats_by_conf": {lbl: rec([p for p in ats if p.get("conf_label") == lbl], "ats") for lbl in ("High", "Medium", "Low")},
        "ats_lean": tier(lean, "lean"), "ats_play": tier(play, "play"), "ats_strong": tier(strong, "strong"), "by_week": by_week,
        "ou": totals(graded, total_edges), "plays": plays(graded),
    }
