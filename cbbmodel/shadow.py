"""Background ("shadow") run for the current season: log picks against the live line, grade them later.

Nothing here is published to the site or the briefings; data/cbb/shadow.json holds the picks and
the running record so we can judge whether college basketball earns a page.
"""
import gzip
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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
        side, prob, ev, tier = backtest.price(hp - ap, -g["spread"], config.MARGIN_SD, odds=None)
        p.update(side="home" if side > 0 else "away", side_prob=round(prob, 3), side_ev=round(ev, 3), side_tier=tier)
        if g.get("total"):
            side, prob, ev, tier = backtest.price(hp + ap, g["total"], config.TOTAL_SD, odds=None)
            p.update(ou="over" if side > 0 else "under", ou_prob=round(prob, 3), ou_ev=round(ev, 3), ou_tier=tier)
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
