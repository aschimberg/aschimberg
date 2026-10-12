"""Season records from a pick log (SU, ATS/ML, by tier, by confidence, by week)."""
from collections import defaultdict


def rec(items, key):
    r = {"W": 0, "L": 0, "P": 0}
    for p in items:
        v = (p.get("result") or {}).get(key)
        if v:
            r[v] += 1
    n = r["W"] + r["L"]
    r["pct"] = round(r["W"] / n, 3) if n else None
    return r


def compute(picks, season, lean, play, strong, period_key=("season", "season_type", "week")):
    graded = [p for p in picks.values() if p.get("result") and (season is None or p["season"] == season)]
    ats = [p for p in graded if p["result"].get("ats")]

    def tier(min_edge):
        return rec([p for p in ats if (p.get("edge") or 0) >= min_edge], "ats")

    weeks = defaultdict(list)
    for p in graded:
        weeks[tuple(p.get(k) for k in period_key)].append(p)
    by_week = [
        {"season": k[0], "season_type": k[1], "week": k[2], "su": rec(items, "su"), "ats": rec(items, "ats"),
         "ats_plays": rec([p for p in items if (p.get("edge") or 0) >= play], "ats")}
        for k, items in sorted(weeks.items(), key=lambda kv: (kv[0][0], kv[0][1] != "regular", kv[0][2]))
    ]
    return {
        "su": rec(graded, "su"), "ats": rec(ats, "ats"),
        "ats_by_conf": {lbl: rec([p for p in ats if p.get("conf_label") == lbl], "ats") for lbl in ("High", "Medium", "Low")},
        "ats_lean": tier(lean), "ats_play": tier(play), "ats_strong": tier(strong), "by_week": by_week,
    }
