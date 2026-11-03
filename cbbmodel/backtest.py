"""Walk-forward backtest of each team's first games of a season against ESPN's closing lines.

Before every game day the ratings are re-solved from games already played plus the preseason
prior (last season's final ratings, regressed), so nothing from the future leaks in.
"""
import math
from collections import defaultdict

from sportscommon import anchor as anc

from . import config, ratings


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def price(model, line, sd, odds=-110):
    """Side, cover chance, EV and tier with the bet priced at the market-anchored number."""
    fair = anc.fair(model, line, *config.ANCHOR)
    side = 1 if model >= line else -1
    p = _phi(side * (fair - line) / sd)
    ev = anc.ev(p, odds)
    return side, p, ev, anc.tier(ev)


def _rec():
    return {"W": 0, "L": 0, "P": 0}


def _add(rec, out):
    rec[out] += 1


def _pct(r):
    n = r["W"] + r["L"]
    return round(r["W"] / n, 3) if n else None


def _summ(r):
    n = r["W"] + r["L"]
    out = dict(r, pct=_pct(r), units=round(r["W"] * 0.909 - r["L"], 1))
    if n:
        out["z"] = round((r["W"] - 0.5238 * n) / math.sqrt(n * 0.25), 2)  # vs the -110 breakeven
    return out


def _slope(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx, 3) if sxx else None


def run(prev_games, games, d1, first=None):
    first = first or config.FIRST_GAMES
    prior_r = ratings.solve(prev_games, d1, priors=None, prior_games=1)
    priors = ratings.carry_over(prior_r, d1)
    played = defaultdict(int)
    done = []
    by_day = defaultdict(list)
    for g in games:
        if g["done"] and g.get("hs") is not None:
            by_day[(g["start"] or "")[:10]].append(g)
    rows = []
    for day in sorted(by_day):
        todays = by_day[day]
        need = [g for g in todays if g["h"] in d1 and g["a"] in d1 and g.get("spread") is not None
                and min(played[g["h"]], played[g["a"]]) < first]
        if need:
            r = ratings.solve(done, d1, priors=priors)
            for g in need:
                pr = ratings.predict(r, g["h"], g["a"], g.get("neutral"))
                if not pr:
                    continue
                hp, ap, poss = pr
                rows.append({"id": g["id"], "day": day, "gn": min(played[g["h"]], played[g["a"]]) + 1,
                             "model_m": hp - ap, "model_t": hp + ap, "line_m": -g["spread"], "line_t": g.get("total"),
                             "res_m": g["hs"] - g["as"], "res_t": g["hs"] + g["as"]})
        for g in todays:
            played[g["h"]] += 1
            played[g["a"]] += 1
        done += todays
    return summarize(rows)


def summarize(rows):
    out = {"games": len(rows)}
    sides = [r for r in rows if r["line_m"] is not None]
    tots = [r for r in rows if r["line_t"]]
    if not sides:
        return out
    mae = lambda xs: round(sum(abs(x) for x in xs) / len(xs), 2)
    sd = lambda xs: round(math.sqrt(sum(x * x for x in xs) / len(xs)), 2)
    out["margin"] = {"model_mae": mae([r["res_m"] - r["model_m"] for r in sides]),
                     "line_mae": mae([r["res_m"] - r["line_m"] for r in sides]),
                     "sd_vs_line": sd([r["res_m"] - r["line_m"] for r in sides]),
                     "slope": _slope([r["model_m"] - r["line_m"] for r in sides], [r["res_m"] - r["line_m"] for r in sides])}
    if tots:
        out["total"] = {"model_mae": mae([r["res_t"] - r["model_t"] for r in tots]),
                        "line_mae": mae([r["res_t"] - r["line_t"] for r in tots]),
                        "sd_vs_line": sd([r["res_t"] - r["line_t"] for r in tots]),
                        "slope": _slope([r["model_t"] - r["line_t"] for r in tots], [r["res_t"] - r["line_t"] for r in tots])}
    msd = out["margin"]["sd_vs_line"]
    tsd = (out.get("total") or {}).get("sd_vs_line") or config.TOTAL_SD
    by_edge = defaultdict(_rec)
    by_tier = defaultdict(_rec)
    by_gn = defaultdict(_rec)
    for r in sides:
        diff = r["model_m"] - r["line_m"]
        res = r["res_m"] - r["line_m"]
        if res == 0 or diff == 0:
            continue
        out_ = "W" if (diff > 0) == (res > 0) else "L"
        _add(by_edge["side " + ("0-2" if abs(diff) < 2 else "2-4" if abs(diff) < 4 else "4-6" if abs(diff) < 6 else "6+")], out_)
        _, _, _, tier = price(r["model_m"], r["line_m"], msd)
        _add(by_tier["side " + tier], out_)
        if tier in ("play", "strong"):
            _add(by_gn["games " + ("1-3" if r["gn"] <= 3 else "4-6" if r["gn"] <= 6 else "7-10")], out_)
    for r in tots:
        diff = r["model_t"] - r["line_t"]
        res = r["res_t"] - r["line_t"]
        if res == 0 or diff == 0:
            continue
        out_ = "W" if (diff > 0) == (res > 0) else "L"
        _add(by_edge["total " + ("0-3" if abs(diff) < 3 else "3-6" if abs(diff) < 6 else "6-9" if abs(diff) < 9 else "9+")], out_)
        _, _, _, tier = price(r["model_t"], r["line_t"], tsd)
        _add(by_tier["total " + tier], out_)
        if tier in ("play", "strong"):
            _add(by_gn["games " + ("1-3" if r["gn"] <= 3 else "4-6" if r["gn"] <= 6 else "7-10")], out_)
    plays = _rec()
    for k, v in by_tier.items():
        if k.endswith("play") or k.endswith("strong"):
            for x in "WLP":
                plays[x] += v[x]
    out["by_edge"] = {k: _summ(v) for k, v in sorted(by_edge.items())}
    out["by_tier"] = {k: _summ(v) for k, v in sorted(by_tier.items())}
    out["plays_by_game_number"] = {k: _summ(v) for k, v in sorted(by_gn.items())}
    out["plays"] = _summ(plays)
    return out
