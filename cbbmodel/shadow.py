"""Background ("shadow") run for the current season: log picks against the live line, grade them later.

Nothing here is published to the site or the briefings; data/cbb/shadow.json holds the picks and
the running record so we can judge whether college basketball earns a page.
"""
import gzip
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import zlib

from sportscommon import anchor as anc
from sportscommon import hoopsim

from . import backtest, config, espn, ratings

PT = timedelta(hours=-8)


def load(path, default):
    try:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    if str(path).endswith(".gz"):
        with gzip.open(path, "wt") as f:
            f.write(espn.dumps(obj))
    else:
        path.write_text(json.dumps(obj, indent=1, sort_keys=True))


def teams():
    path = config.DATA_DIR / "teams.json"
    t = load(path, {})
    if not t:
        t = espn.d1_teams()
        save(path, t)
    return t


def season_now(now=None):
    now = now or datetime.now(timezone.utc)
    return now.year + 1 if now.month >= 7 else now.year


def priors_for(season, d1):
    path = config.DATA_DIR / f"priors_{season}.json"
    p = load(path, None)
    if p:
        return {k: tuple(v) for k, v in p.items()}
    prev = load(config.DATA_DIR / f"games_{season - 1}.json.gz", [])
    if not prev:
        return None
    before = load(config.DATA_DIR / f"games_{season - 2}.json.gz", [])
    pr = ratings.carry_over(ratings.solve(before, d1, prior_games=1), d1) if before else None
    p = ratings.carry_over(ratings.solve(prev, d1, priors=pr, prior_games=2 if pr else 1), d1)
    save(path, p)
    return p


def styles(games, d1):
    """Each team's shooting style from its box scores this season, blended with the D1 average."""
    base = hoopsim.LEAGUES["cbb"]["style"]
    acc = defaultdict(lambda: defaultdict(float))
    for g in games:
        for side, opp in (("hb", "ab"), ("ab", "hb")):
            b, o = g.get(side), g.get(opp)
            if not b or not o:
                continue
            a = acc[g["h"] if side == "hb" else g["a"]]
            for k in ("fga", "tpa", "tpm", "fta", "ftm", "orb", "tov", "poss", "fgm"):
                a[k] += b[k]
            a["opp_drb"] += o["drb"]
            a["n"] += 1
    out = {}
    for t, a in acc.items():
        if t not in d1 or not a["fga"] or not a["poss"]:
            continue
        w = a["n"] / (a["n"] + 8)
        mix = lambda v, k: round(w * v + (1 - w) * base[k], 4)
        out[t] = {"tov": mix(a["tov"] / a["poss"], "tov"), "foul": mix(a["fta"] / 2 / max(a["fga"] + a["fta"] / 2, 1), "foul"),
                  "three": mix(a["tpa"] / a["fga"], "three"), "p3": base["p3"], "p2": base["p2"],
                  "ft": mix(a["ftm"] / max(a["fta"], 1), "ft"), "orb": mix(a["orb"] / max(a["orb"] + a["opp_drb"], 1), "orb")}
    return out


def simulate(hp, ap, poss, hs, as_, gid, n=None):
    """Play the game out possession by possession at the projected scores."""
    return hoopsim.run("cbb", hp, ap, poss, n or config.SIMS, seed=zlib.crc32(str(gid).encode()),
                       home_style=hs, away_style=as_)


def sim_price(res, model, line, kind, odds=None):
    """Side, cover/over chance (pushes excluded), push chance, EV and tier from simulated games."""
    vals = res["margins"] if kind == "side" else res["totals"]
    n = len(vals)
    side = 1 if model >= line else -1
    win = sum(1 for v in vals if (v - line) * side > 0) / n
    push = sum(1 for v in vals if v == line) / n
    ev = anc.ev(win, odds, push)
    return side, round(win / (1 - push), 3) if push < 1 else None, round(push, 3), ev, anc.tier(ev)


def _grade(p, g):
    m, t = g["hs"] - g["as"], g["hs"] + g["as"]
    res = {}
    if p.get("side"):
        cover = m + p["spread"] if p["side"] == "home" else -(m + p["spread"])
        res["side"] = "W" if cover > 0 else "L" if cover < 0 else "P"
    if p.get("ou"):
        d = t - p["total"]
        res["total"] = ("W" if (d > 0) == (p["ou"] == "over") else "L") if d else "P"
    return res


def run(now=None):
    now = now or datetime.now(timezone.utc)
    season = season_now(now)
    d1 = teams()
    gpath = config.DATA_DIR / f"games_{season}.json.gz"
    old = {g["id"]: g for g in load(gpath, [])}
    games = espn.season_games(season, d1)
    for g in games:
        o = old.get(g["id"])
        if o:
            for k in ("hp", "ap", "hb", "ab"):
                if k in o:
                    g[k] = o[k]
    espn.fill(games)
    save(gpath, games)
    by_id = {g["id"]: g for g in games}

    priors = priors_for(season, d1)
    r = ratings.solve([g for g in games if g["done"]], d1, priors=priors)
    played = r["games"]
    sty = styles([g for g in games if g["done"]], d1)

    spath = config.DATA_DIR / "shadow.json"
    state = load(spath, {"picks": {}})
    picks = state["picks"]
    today = (now + PT).strftime("%Y%m%d")
    tomorrow = (now + PT + timedelta(days=1)).strftime("%Y%m%d")
    for g in espn.scoreboard(today) + espn.scoreboard(tomorrow):
        if g["done"] or g.get("spread") is None or g["h"] not in d1 or g["a"] not in d1:
            continue
        if g["start"] and datetime.fromisoformat(g["start"].replace("Z", "+00:00")) <= now:
            continue  # keep the last pre-game line we saw
        pr = ratings.predict(r, g["h"], g["a"], g["neutral"])
        if not pr:
            continue
        hp, ap, poss = pr
        p = {"id": g["id"], "start": g["start"], "home": d1[g["h"]]["name"], "away": d1[g["a"]]["name"],
             "spread": g["spread"], "total": g.get("total"), "book": g.get("book"),
             "model_m": round(hp - ap, 1), "model_t": round(hp + ap, 1), "poss": round(poss, 1),
             "gn": min(played.get(g["h"], 0), played.get(g["a"], 0)) + 1, "logged": now.isoformat(timespec="minutes")}
        # price at the market-anchored margin and total, played out possession by possession
        fair_m = anc.fair(hp - ap, -g["spread"], *config.ANCHOR)
        fair_t = anc.fair(hp + ap, g["total"], *config.ANCHOR) if g.get("total") else hp + ap
        res = simulate((fair_t + fair_m) / 2, (fair_t - fair_m) / 2, poss, sty.get(g["h"]), sty.get(g["a"]), g["id"])
        side, prob, push, ev, tier = sim_price(res, hp - ap, -g["spread"], "side")
        p.update(side="home" if side > 0 else "away", side_prob=prob, side_push=push, side_ev=round(ev, 3), side_tier=tier)
        if g.get("total"):
            side, prob, push, ev, tier = sim_price(res, hp + ap, g["total"], "total")
            p.update(ou="over" if side > 0 else "under", ou_prob=prob, ou_push=push, ou_ev=round(ev, 3), ou_tier=tier)
        picks[g["id"]] = p

    for pid, p in picks.items():
        g = by_id.get(pid)
        if g and g["done"] and g.get("hs") is not None and "result" not in p:
            p["result"] = _grade(p, g)
            p["score"] = f"{g['as']}-{g['hs']}"
    state["summary"] = summary(picks.values())
    state["updated"] = now.isoformat(timespec="minutes")
    save(spath, state)
    s = state["summary"]
    print(f"cbb shadow: {len(picks)} picks logged; plays {s['plays']}; all sides {s['sides']['all']}; totals {s['totals']['all']}")


def summary(picks):
    rec = lambda: {"W": 0, "L": 0, "P": 0}
    sides, totals, plays = defaultdict(rec), defaultdict(rec), rec()
    for p in picks:
        res = p.get("result") or {}
        for kind, tier_key, store in (("side", "side_tier", sides), ("total", "ou_tier", totals)):
            out = res.get(kind)
            if out not in ("W", "L", "P"):
                continue
            tier = p.get(tier_key, "pass")
            store["all"][out] += 1
            store[tier][out] += 1
            if tier in ("play", "strong"):
                plays[out] += 1
    return {"sides": {k: backtest._summ(v) for k, v in sides.items()} or {"all": backtest._summ(rec())},
            "totals": {k: backtest._summ(v) for k, v in totals.items()} or {"all": backtest._summ(rec())},
            "plays": backtest._summ(plays)}
