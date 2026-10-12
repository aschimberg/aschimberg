"""Walk-forward CFB backtest: what the model would have picked each week.

CFBD serves SP+ and FPI only as end-of-season values (asking for a past week
returns the final numbers, which already "know" the results), so a fair
backtest rebuilds each source as it stood before that week:

- Elo: CFBD's Elo as of the previous week (point-in-time);
- FPI: ESPN's pregame win probability for the game (FPI-based, published
  before kickoff), converted to a margin: sigma * inv_normal(p) - home field;
- SP+ (stand-in): opponent-adjusted scoring margin from that season's earlier
  games, shrunk toward last season's final SP+ (regressed like the live prior).
  Real in-season SP+ also uses play-by-play efficiency, so this is a weaker
  version of it.

Each week is priced against the closing line with the live pick and
confidence logic, then the weekly tuner runs on that season's graded picks.

    python -m cfbmodel backtest [--seasons 2024-2026] [--apply]
"""
import math
from datetime import datetime, timezone
from statistics import mean, pstdev

from sportscommon import tuning as tunemod

from . import ats, config, confidence, model, store
from .cfbd import g
from .normalize import num

BT_CACHE = "backtest"
FPI_HFA = 2.5  # home field inside ESPN's pregame probabilities (points)
SP_PRIOR_GAMES = 4.0  # games of evidence behind last season's SP+
MARGIN_CAP = 35.0  # blowout cap for the stand-in's margins
FCS = "FCS"


def backtest_path():
    return config.DATA_DIR / "backtest.json"


def site_backtest_path():
    return config.SITE_DATA_DIR / "backtest.json"


def _cached(name, fetch):
    path = config.CACHE_DIR / BT_CACHE / f"{name}.json"
    doc = store.load(path, None)
    if doc is None:
        doc = fetch()
        store.save(path, doc, compact=True)
    return doc


def inv_normal(p):
    lo, hi = -6.0, 6.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if 0.5 * (1 + math.erf(mid / math.sqrt(2))) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def stand_in(games, fbs, prior, lam=SP_PRIOR_GAMES, iters=60):
    """Opponent-adjusted margin rating (points) with a prior, by Gauss-Seidel.

    games: completed history records before the week; non-FBS teams pool as FCS.
    prior: {team: rating}. Returns ({team: rating}, hfa).
    """
    def key(t):
        return t if t in fbs else FCS

    obs = []
    for gm in games:
        if gm["home_pts"] is None or gm["away_pts"] is None:
            continue
        h, a = key(gm["home"]), key(gm["away"])
        if h == a:
            continue
        m = gm["home_pts"] - gm["away_pts"]
        obs.append((h, a, 0 if gm["neutral"] else 1, max(-MARGIN_CAP, min(MARGIN_CAP, m))))
    teams = set(fbs) | {FCS}
    r = {t: prior.get(t, config.NON_FBS_RATING if t == FCS else 0.0) for t in teams}
    by_team = {t: [] for t in teams}
    for i, (h, a, f, m) in enumerate(obs):
        by_team[h].append(i)
        by_team[a].append(i)
    hfa = config.HOME_FIELD
    for _ in range(iters):
        for t in teams:
            num_, den = lam * prior.get(t, r[t]), lam
            for i in by_team[t]:
                h, a, f, m = obs[i]
                if t == h:
                    num_ += m - hfa * f + r[a]
                else:
                    num_ += -m + hfa * f + r[h]
                den += 1
            r[t] = num_ / den
        fs = [o for o in obs if o[2]]
        if len(fs) >= 20:
            hfa = (sum(m - (r[h] - r[a]) for h, a, f, m in fs) + 20 * config.HOME_FIELD) / (len(fs) + 20)
    return r, hfa


def _prior_sp(prior_ratings, fbs):
    sp = {g(x, "team"): num(g(x, "rating")) for x in (prior_ratings or {}).get("sp", [])}
    sp = {t: v for t, v in sp.items() if v is not None}
    pool = [v for t, v in sp.items() if t in fbs] or list(sp.values()) or [0.0]
    c = mean(pool)
    out = {t: c + (v - c) * config.PRIOR_SEASON_REGRESSION for t, v in sp.items()}
    out[FCS] = config.NON_FBS_RATING
    return out


def _elo_points(elo_raw, fbs, target):
    """CFBD Elo mapped onto the stand-in's FBS mean/sd (as live maps Elo onto SP+)."""
    elo = {g(x, "team"): num(g(x, "elo")) for x in elo_raw or []}
    elo = {t: v for t, v in elo.items() if v is not None}
    pool_e = [elo[t] for t in fbs if t in elo]
    pool_t = [target[t] for t in fbs if t in target]
    if len(pool_e) < 10 or len(pool_t) < 10:
        return {}
    me, se = mean(pool_e), pstdev(pool_e) or 1.0
    mt, st = mean(pool_t), pstdev(pool_t) or 1.0
    return {t: mt + (v - me) / se * st for t, v in elo.items()}


def run(seasons, client=None, now=None, log=print, start_weights=None, start_hfa=None, current_games=None):
    """Walk-forward over `seasons`. CFBD responses are cached under data/cache/backtest."""
    from .pipeline import fetch_season_history, load_history, load_prior_ratings, season_for

    now = now or datetime.now(timezone.utc)
    current = season_for(now)
    saved = (dict(config.WEIGHTS), config.HOME_FIELD)
    weights = dict(start_weights or config.WEIGHTS)
    hfa0 = config.HOME_FIELD if start_hfa is None else start_hfa
    picks, history, weeks_out, notes = {}, [], [], []
    try:
        config.WEIGHTS.clear()
        config.WEIGHTS.update(weights)
        config.HOME_FIELD = hfa0
        for season in seasons:
            anchor = {"weights": dict(config.WEIGHTS), "home_field": config.HOME_FIELD}
            if season == current:
                snap = config.CACHE_DIR / BT_CACHE / f"games_{season}_current.json"
                if current_games is not None:
                    games = current_games
                elif client is None:
                    games = store.load(snap, [])  # offline rerun
                else:
                    games = fetch_season_history(client, season)
                    store.save(snap, games, compact=True)
            else:
                games = load_history(client, [season], log=log)
            fbs = set(_cached(f"fbs_{season}", lambda: [g(t, "school") for t in client.fbs_teams(season)]))
            prior_sp = _prior_sp(load_prior_ratings(client, season - 1), fbs)
            prior_elo = (load_prior_ratings(client, season - 1) or {}).get("elo", [])
            wp = {}
            for st in ("regular", "postseason"):
                rows = _cached(f"wp_{season}_{st}", lambda: client.get("/metrics/wp/pregame", year=season, seasonType=st))
                for x in rows or []:
                    p = num(g(x, "homeWinProbability"))
                    if p is not None:
                        wp[g(x, "gameId")] = min(0.995, max(0.005, p))
            hist = load_history(client, list(range(season - config.TREND_SEASONS, season)), log=log)
            rest_map = ats.previous_game_dates(games)
            reg_weeks = sorted({gm["week"] for gm in games if gm["season_type"] == "regular"})
            order = [("regular", w) for w in reg_weeks] + \
                    [("postseason", w) for w in sorted({gm["week"] for gm in games if gm["season_type"] == "postseason"})]
            done_before = []
            for stype, week in order:
                wk = [gm for gm in games if gm["season_type"] == stype and gm["week"] == week]
                priced = [gm for gm in wk if gm["completed"] and gm["spread"] is not None
                          and gm["home_pts"] is not None and (gm["home"] in fbs or gm["away"] in fbs)]
                if priced:
                    sp, _ = stand_in(done_before, fbs, prior_sp)
                    if stype == "regular" and week > 1:
                        elo_raw = _cached(f"elo_{season}_r{week - 1}",
                                          lambda: client.elo(season, week=week - 1, season_type="regular"))
                    elif stype == "postseason":
                        last = reg_weeks[-1]
                        elo_raw = _cached(f"elo_{season}_r{last}", lambda: client.elo(season, week=last, season_type="regular"))
                    else:
                        elo_raw = prior_elo
                    elo = _elo_points(elo_raw, fbs, sp)
                    trends = ats.compute_trends(hist + [x for x in done_before if x.get("spread") is not None])
                    played = {}
                    for x in done_before:
                        played[x["home"]] = played.get(x["home"], 0) + 1
                        played[x["away"]] = played.get(x["away"], 0) + 1

                    def rating(t, src):
                        k = t if t in fbs else FCS
                        if k in src:
                            return src[k]
                        return sp.get(k, config.NON_FBS_RATING if k == FCS else 0.0)

                    wk_picks = []
                    for gm in priced:
                        h, a = gm["home"], gm["away"]
                        hflag = 0 if gm["neutral"] else 1
                        d_sp = rating(h, sp) - rating(a, sp)
                        d_elo = rating(h, elo) - rating(a, elo) if elo else d_sp
                        if gm["id"] in wp:
                            d_fpi = config.MARGIN_SD * inv_normal(wp[gm["id"]]) - FPI_HFA * hflag
                        else:
                            d_fpi = (d_sp + d_elo) / 2
                        diffs = {"sp": round(d_sp, 2), "fpi": round(d_fpi, 2), "elo": round(d_elo, 2)}
                        margin = sum(config.WEIGHTS[k] * diffs[k] for k in model.SOURCES) + config.HOME_FIELD * hflag
                        proj = {"margin": round(margin, 1), "spread": round(-margin, 1), "home_wp": round(model.win_prob(margin), 3)}
                        line = {"spread": gm["spread"]}
                        pick = model.make_pick(gm, proj, line)
                        trend_block = {}
                        for side in ("home", "away"):
                            ts = gm["spread"] if side == "home" else -gm["spread"]
                            trend_block[side] = ats.relevant_trends(trends, gm[side], gm, ts, rest_map.get((gm[side], gm["id"])))
                        dq = []
                        for t in (h, a):
                            n = played.get(t, 0)
                            dq.append(0.3 if t not in fbs else min(1.0, 0.6 + 0.1 * n))
                        conf = confidence.score(pick, gm["spread"], diffs, hflag, None, trend_block, None, sum(dq) / 2)
                        pick["confidence"] = conf["score"] if conf else None
                        pick["conf_label"] = conf["label"] if conf else None
                        hp, ap = gm["home_pts"], gm["away_pts"]
                        winner = h if hp > ap else a if ap > hp else None
                        p = {
                            "id": gm["id"], "season": season, "week": week, "season_type": stype, "start": gm["start"],
                            "home": h, "away": a, "neutral": gm["neutral"], "line": gm["spread"],
                            "book": "CFBD closing consensus", "model_spread": proj["spread"],
                            "diffs": diffs, "hfa_flag": hflag, "weights": dict(config.WEIGHTS),
                            "home_field": config.HOME_FIELD, **pick, "confidence_detail": conf, "backtest": True,
                            "result": {"home_pts": hp, "away_pts": ap,
                                       "su": "P" if winner is None else ("W" if winner == pick["su_team"] else "L"),
                                       "ats": model.grade(pick["ats_side"], gm["spread"], hp, ap)},
                        }
                        picks[str(gm["id"])] = p
                        wk_picks.append(p)

                    rows = [(p["diffs"], p["hfa_flag"], p["result"]["home_pts"] - p["result"]["away_pts"])
                            for p in picks.values() if p["season"] == season]
                    at = max(gm["start"] for gm in priced)
                    entry = tunemod.tune(rows, {k: config.WEIGHTS[k] for k in model.SOURCES}, config.HOME_FIELD,
                                         config.MIN_TUNE_GAMES, config.TUNE_PRIOR_GAMES, (0.0, 5.0),
                                         now=datetime.fromisoformat(at.replace("Z", "+00:00")), anchor=anchor)
                    entry.update({"season": season, "week": week, "season_type": stype, "backtest": True})
                    history.append(entry)
                    if entry["status"] == "updated":
                        config.WEIGHTS.update(entry["after"]["weights"])
                        config.HOME_FIELD = entry["after"]["home_field"]
                    wr = _record(wk_picks)
                    weeks_out.append({"season": season, "week": week, "season_type": stype, "n": len(wk_picks), **wr,
                                      "tune": entry["status"], "weights": dict(config.WEIGHTS), "home_field": config.HOME_FIELD})
                    log(f"{season} {stype[:4]} wk {week:>2}: {len(wk_picks)} picks, ATS {wr['ats']['W']}-{wr['ats']['L']}"
                        f", plays {wr['ats_play']['W']}-{wr['ats_play']['L']} | tune {entry['status']} -> "
                        f"{config.WEIGHTS} HFA {config.HOME_FIELD}")
                done_before += [gm for gm in wk if gm["completed"]]
        final = {"weights": dict(config.WEIGHTS), "home_field": config.HOME_FIELD}
    finally:
        config.WEIGHTS.clear()
        config.WEIGHTS.update(saved[0])
        config.HOME_FIELD = saved[1]

    summary = {str(s): _record([p for p in picks.values() if p["season"] == s]) for s in seasons}
    summary["all"] = _record(list(picks.values()))
    return {"seasons": list(seasons), "picks": picks, "history": history, "weeks": weeks_out, "summary": summary,
            "final": final, "start": {"weights": weights, "home_field": hfa0}, "notes": notes,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}


def _rec(items):
    w = sum(1 for p in items if p["result"]["ats"] == "W")
    l_ = sum(1 for p in items if p["result"]["ats"] == "L")
    pu = sum(1 for p in items if p["result"]["ats"] == "P")
    return {"W": w, "L": l_, "P": pu, "pct": round(w / (w + l_), 3) if w + l_ else None}


def _record(items):
    su_w = sum(1 for p in items if p["result"]["su"] == "W")
    su_l = sum(1 for p in items if p["result"]["su"] == "L")
    return {
        "ats": _rec(items),
        "ats_lean": _rec([p for p in items if (p.get("edge") or 0) >= config.LEAN_EDGE]),
        "ats_play": _rec([p for p in items if (p.get("edge") or 0) >= config.PLAY_EDGE]),
        "ats_strong": _rec([p for p in items if (p.get("edge") or 0) >= config.STRONG_EDGE]),
        "ats_by_conf": {lbl: _rec([p for p in items if p.get("conf_label") == lbl]) for lbl in ("High", "Medium", "Low")},
        "su": {"W": su_w, "L": su_l, "P": 0, "pct": round(su_w / (su_w + su_l), 3) if su_w + su_l else None},
    }


def site_doc(res):
    keep = ("id", "season", "week", "season_type", "start", "home", "away", "line", "model_spread",
            "ats_team", "ats_line", "edge", "tier", "confidence", "conf_label", "su_team")
    return {
        "sport": "cfb", "generated_at": res["generated_at"], "seasons": res["seasons"], "summary": res["summary"],
        "weeks": res["weeks"], "start": res["start"], "final": res["final"],
        "picks": [dict({k: p.get(k) for k in keep}, result={k: p["result"][k] for k in ("ats", "su", "home_pts", "away_pts")})
                  for p in sorted(res["picks"].values(), key=lambda p: (p["season"], p["season_type"] != "regular", p["week"], p["start"] or ""))],
        "method": "Walk-forward: each week rebuilt only from data available before kickoff. Elo is CFBD's as of the "
                  "prior week; FPI is ESPN's pregame win probability converted to a margin; SP+ is a stand-in "
                  "(opponent-adjusted scoring margin shrunk toward last season's SP+), because CFBD only serves "
                  "end-of-season SP+. Priced against the closing line, then the weekly tuner runs.",
    }


def save(res):
    slim = ("id", "season", "week", "season_type", "home", "away", "line", "model_spread", "diffs", "hfa_flag",
            "edge", "ats_side", "confidence", "conf_label", "result")
    store.save(backtest_path(), {k: v for k, v in res.items() if k != "picks"} | {
        "n_picks": len(res["picks"]), "picks": [{k: p.get(k) for k in slim} for p in res["picks"].values()]}, compact=True)
    store.save(site_backtest_path(), site_doc(res), compact=True)


def apply(res, now=None, log=print, adopt_weights=False):
    """Add this season's backtest picks the live log doesn't have and adopt the
    backtest's home field (and, with adopt_weights, its source weights).

    Source weights default to staying with live tuning: the backtest's SP+
    column is a stand-in, so a weight learned on it would understate real SP+.
    """
    from . import tuning
    from .pipeline import PICKS_PATH, season_for
    now = now or datetime.now(timezone.utc)
    season = season_for(now)
    doc = store.load(PICKS_PATH, {"picks": {}})
    added = 0
    for gid, p in res["picks"].items():
        if p["season"] == season and gid not in doc["picks"]:
            doc["picks"][gid] = p
            added += 1
    store.save(PICKS_PATH, doc)
    w = tuning.load() or {"history": [], "weights": dict(config.WEIGHTS), "home_field": config.HOME_FIELD}
    w["home_field"] = res["final"]["home_field"]
    if adopt_weights:
        w["weights"] = res["final"]["weights"]
    w["backtest"] = {"seasons": res["seasons"], "final": res["final"], "applied": "weights+home field" if adopt_weights else "home field",
                     "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    w["updated"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    w.setdefault("weights", dict(config.WEIGHTS))
    store.save(tuning.path(), w)
    log(f"Applied CFB backtest: +{added} {season} picks; home field {w['home_field']}; weights {w['weights']}")
    return added
