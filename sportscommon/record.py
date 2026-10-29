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


BREAKEVEN = 0.5238  # win rate needed at -110

# Picks made by the current model version (market anchoring + NHL sim fix);
# the retune check judges these, not picks from older versions.
CURRENT_MODEL_SINCE = "2026-10-28"
CURRENT_MODEL_LABEL = "Oct 28 model"
HEALTH_MIN_PLAYS = 25


def _claimed(p, prob):
    """The win chance the model claimed for a pick: its logged probability, or at
    least breakeven for old logs without one (a play claims an edge)."""
    return prob if prob else BREAKEVEN


def plays(items):
    """Every pick rated play or strong, by the tier it was logged with: sides
    (spread or moneyline) and totals separately, plus both together. Units use
    the logged payout, else the pick's price, else -110. Also the win rate the
    model claimed for those picks and a z-score of actual vs claimed."""
    side_r, total_r = _part(), _part()
    exp = var = 0.0
    for p in items:
        res = p.get("result") or {}
        side = res.get("ats")
        if p.get("tier") in PLAY_TIERS and side in ("W", "L", "P"):
            side_r[side] += 1
            side_r["units"] += res["units"] if res.get("units") is not None else _units(side, p.get("odds"))
            if side != "P":
                q = _claimed(p, p.get("cover_prob") or p.get("prob"))
                exp, var = exp + q, var + q * (1 - q)
        t = p.get("total_pick") or {}
        tot = res.get("total")
        if t.get("tier") in PLAY_TIERS and tot in ("W", "L", "P"):
            total_r[tot] += 1
            total_r["units"] += res["total_units"] if res.get("total_units") is not None else _units(tot, t.get("odds"))
            if tot != "P":
                q = _claimed(t, t.get("prob"))
                exp, var = exp + q, var + q * (1 - q)
    r = {k: side_r[k] + total_r[k] for k in ("W", "L", "P", "units")}
    _finish(side_r)
    _finish(total_r)
    r["sides"] = side_r["W"] + side_r["L"] + side_r["P"]
    r["totals"] = total_r["W"] + total_r["L"] + total_r["P"]
    r["side"], r["total"] = side_r, total_r
    n = r["W"] + r["L"]
    r["exp"] = round(exp / n, 3) if n else None
    r["z"] = round((r["W"] - exp) / var ** 0.5, 2) if var else None
    return _finish(r)


def health(r, min_plays=HEALTH_MIN_PLAYS):
    """Model check on a plays record.

    retune: plays won significantly less than the model claimed (z <= -2,
            about a 1-in-40 chance if the claimed rates were right)
    watch:  below the -110 breakeven, but not yet significant
    ok:     at or above breakeven
    early:  fewer than min_plays graded
    """
    n = (r or {}).get("W", 0) + (r or {}).get("L", 0)
    if n < min_plays:
        return {"status": "early", "n": n, "need": min_plays,
                "note": f"{n} of {min_plays} plays graded"}
    pct, exp, z = r["pct"], r.get("exp"), r.get("z")
    detail = f"{pct * 100:.1f}% vs {exp * 100:.1f}% predicted" if exp else f"{pct * 100:.1f}%"
    if z is not None and z <= -2:
        return {"status": "retune", "n": n, "note": f"Retune flagged: {detail} (z {z:+.1f})"}
    if pct < BREAKEVEN:
        return {"status": "watch", "n": n, "note": f"Watch: {detail}, below the 52.4% breakeven"}
    return {"status": "ok", "n": n, "note": f"On track: {detail}"}


def current_model(items):
    """Picks whose game starts on or after CURRENT_MODEL_SINCE."""
    return [p for p in items if (p.get("start") or p.get("date") or "") >= CURRENT_MODEL_SINCE]


def plays_block(graded):
    """Season plays plus the current model's plays and its retune check."""
    cur_items = current_model(graded)
    cur_ids = {id(p) for p in cur_items}
    cur = plays(cur_items)
    prev = plays([p for p in graded if id(p) not in cur_ids])
    return plays(graded), {"since": CURRENT_MODEL_SINCE, "label": CURRENT_MODEL_LABEL, "plays": cur,
                           "health": health(cur), "previous": prev, "previous_health": health(prev)}


def plays_keys(graded):
    season, model = plays_block(graded)
    return {"plays": season, "model_check": model}


def check_line(sr):
    """Briefing warning when the current model's plays need attention, else None."""
    h = ((sr or {}).get("model_check") or {}).get("health") or {}
    if h.get("status") in ("retune", "watch"):
        return f"⚠ Model check: {h['note']}"
    return None


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
        "ou": totals(graded, total_edges), **plays_keys(graded),
    }
