"""Walk-forward NFL backtest: what the model would have picked each week.

For every week of the chosen seasons the model is rebuilt from data that
existed before that week's kickoffs and nothing later:

- EPA ratings: play-by-play from earlier weeks of the season, with last
  season's full-season fit as the prior (same as live);
- Elo: pregame ratings from the sequential Elo run (no look-ahead);
- QB values and team baselines: dropbacks before that week (plus the two
  prior seasons), with the game's actual starter from nflverse;
- key-number margin weights: final margins from earlier seasons only;
- ATS trends: results before that week;
- line: nflverse's closing consensus spread (picks lock at kickoff).

After each week the weekly tuner runs on that season's graded picks, exactly
as the live Tuesday job does, so the weights evolve the way they would have.

    python -m nflmodel backtest [--seasons 2023-2026] [--apply]
"""
from datetime import datetime, timezone

from cfbmodel import ats, store
from cfbmodel.model import grade as ats_grade
from sportscommon import record as recmod
from sportscommon import drivesim, totals
from sportscommon import tuning as tunemod

from . import config, data, elo as elomod, margins, pipeline, qb as qbmod, ratings, teams



def backtest_path():
    return config.DATA_DIR / "backtest.json"


def site_backtest_path():
    return config.SITE_DATA_DIR / "backtest.json"


def _wk(season_type, week):
    return week + (30 if season_type == "postseason" else 0)


def data_offline():
    """NFL_BACKTEST_OFFLINE=1: use the saved games/pbp snapshots instead of the network."""
    import os
    return os.environ.get("NFL_BACKTEST_OFFLINE") == "1"


def run(seasons, games=None, pbp_loader=None, now=None, log=print, start_weights=None, start_hfa=None):
    now = now or datetime.now(timezone.utc)
    offline = games is None and pbp_loader is None and data_offline()
    games = games if games is not None else (data.load_games_snapshot() if offline else data.load_games(snapshot=True))
    first = min(seasons)
    current = pipeline.season_for(now)
    partial = config.CACHE_DIR / f"pbp_{current}_partial.json"

    def default_loader(s):
        if s == current:
            if offline:
                return store.load(partial, {"team_games": [], "qb_games": []})
            agg = data.load_pbp(s, current, log=log)
            store.save(partial, agg, compact=True)  # lets backtests rerun offline
            return agg
        return data.load_pbp(s, current, log=log)

    loader = pbp_loader or default_loader
    pbp = {s: loader(s) for s in range(first - 2, max(seasons) + 1)}

    saved = (dict(config.WEIGHTS), config.HOME_FIELD)
    weights = dict(start_weights or config.WEIGHTS)
    hfa = config.HOME_FIELD if start_hfa is None else start_hfa
    picks, history, weeks_out = {}, [], []
    try:
        config.WEIGHTS.clear()
        config.WEIGHTS.update(weights)
        config.HOME_FIELD = hfa
        _, pregame = elomod.run(games)
        for season in seasons:
            anchor = {"weights": dict(config.WEIGHTS), "home_field": config.HOME_FIELD}  # season start
            sg = [g for g in games if g["season"] == season]
            kw = margins.key_weights([g for g in games if g["season"] < season])
            prior_fit = ratings.fit_season(pbp[season - 1]["team_games"], decay=1.0)
            rest_map = ats.previous_game_dates([pipeline._history_record(g) for g in sg])
            week_keys = sorted({(_wk(g["season_type"], g["week"]), g["season_type"], g["week"]) for g in sg})
            for key, stype, week in week_keys:
                wk_games = [g for g in sg if g["season_type"] == stype and g["week"] == week
                            and g["completed"] and g["spread"] is not None]
                if not wk_games:
                    continue
                tg = [t for t in pbp[season]["team_games"] if _wk(t["season_type"], t["week"]) < key]
                fit = ratings.fit_season(tg, prior=prior_fit) if tg else ratings.fit_season([], prior=prior_fit)
                # nflverse numbers postseason weeks 19-22, so week order works across both
                qcur = [q for q in pbp[season]["qb_games"] if q["week"] < week]
                qb_seasons = [qcur, pbp[season - 1]["qb_games"], pbp[season - 2]["qb_games"]]
                db_rows = qcur + pbp[season - 1]["qb_games"]
                tot = sum(q["db"] for q in db_rows)
                league_db = sum(q["epa"] for q in db_rows) / tot if tot else 0.0
                qb_values, replacement = qbmod.qb_values(qb_seasons, league_db)
                baselines = qbmod.team_baselines(qb_seasons, qb_values)
                kickoff = min(g["start"] for g in wk_games)
                hist = [pipeline._history_record(g) for g in games
                        if season - config.TREND_SEASONS <= g["season"] <= season and g["completed"]
                        and (g["start"] or "") < kickoff]
                trends = ats.compute_trends(hist)
                played = {}
                for g in sg:
                    if g["completed"] and _wk(g["season_type"], g["week"]) < key:
                        played[g["home"]] = played.get(g["home"], 0) + 1
                        played[g["away"]] = played.get(g["away"], 0) + 1

                # league scoring so far this season (last season's before any games)
                done = [x for x in sg if x["completed"] and _wk(x["season_type"], x["week"]) < key] or \
                       [x for x in games if x["season"] == season - 1 and x["completed"]]
                league_ppg = sum(x["home_pts"] + x["away_pts"] for x in done) / (2 * len(done)) if done else 22.0

                wk_picks = []
                for g in wk_games:
                    h, a = g["home"], g["away"]
                    diffs, offset, hflag, _, _ = pipeline.margin_parts(g, fit, pregame, qb_values, baselines, replacement)
                    margin = sum(config.WEIGHTS[k] * diffs[k] for k in pipeline.SOURCES) + config.HOME_FIELD * hflag + offset
                    model_total = pipeline.project_total(fit, h, a, league_ppg, margin)
                    wp = drivesim.summary(drivesim.run_at("nfl", margin, model_total, n=config.SIMS_BACKTEST,
                                                          seed=g["id"]))["home_wp"]
                    s = g["spread"]
                    trend_block = {}
                    for side in ("home", "away"):
                        ts = s if side == "home" else -s
                        trend_block[side] = pipeline._relabel(ats.relevant_trends(
                            trends, g[side], pipeline._history_record(g), ts, rest_map.get((g[side], g["id"]))))
                    qb_known = bool(g["home_qb_id"] and g["away_qb_id"])
                    dq = min(1.0, min(played.get(h, 0), played.get(a, 0)) / 4) * (1.0 if qb_known else 0.7)
                    fields, conf_detail = pipeline.ats_pick(g, margin, model_total, diffs, offset, hflag, s, None, trend_block, dq,
                                                           sims=config.SIMS_BACKTEST)
                    hp, ap = g["home_pts"], g["away_pts"]
                    tp = pipeline.total_pick(model_total, g["total"], margin, g["id"], sims=config.SIMS_BACKTEST)
                    su_team = teams.name(h if margin >= 0 else a)
                    winner = teams.name(h) if hp > ap else teams.name(a) if ap > hp else None
                    p = {
                        "id": g["id"], "season": season, "week": week, "season_type": stype, "start": g["start"],
                        "home": teams.name(h), "away": teams.name(a), "home_abbr": h, "away_abbr": a,
                        "neutral": g["neutral"], "line": s, "book": "nflverse closing consensus",
                        "total_line": g["total"], "model_spread": round(-margin, 1), "model_total": round(model_total, 1),
                        "total_pick": tp,
                        "home_qb": g.get("home_qb"), "away_qb": g.get("away_qb"),
                        "diffs": diffs, "hfa_flag": hflag, "offset": round(offset, 2),
                        "weights": dict(config.WEIGHTS), "home_field": config.HOME_FIELD,
                        "su_team": su_team, "su_wp": round(wp if margin >= 0 else 1 - wp, 3),
                        **fields, "confidence_detail": conf_detail, "backtest": True,
                        "result": {"home_pts": hp, "away_pts": ap,
                                   "su": "P" if winner is None else ("W" if winner == su_team else "L"),
                                   "ats": ats_grade(fields["ats_side"], s, hp, ap),
                                   "total": totals.grade(tp, hp + ap)},
                    }
                    picks[g["id"]] = p
                    wk_picks.append(p)

                # weekly tuning on this season's graded picks, as the live job does
                rows = [(p["diffs"], p["hfa_flag"], (p["result"]["home_pts"] - p["result"]["away_pts"]) - p["offset"])
                        for p in picks.values() if p["season"] == season]
                at = max(g["start"] for g in wk_games)
                entry = tunemod.tune(rows, {k: config.WEIGHTS[k] for k in pipeline.SOURCES}, config.HOME_FIELD,
                                     config.MIN_TUNE_GAMES, config.TUNE_PRIOR_GAMES, (0.0, 4.0),
                                     now=datetime.fromisoformat(at.replace("Z", "+00:00")), anchor=anchor)
                entry.update({"season": season, "week": week, "season_type": stype, "backtest": True})
                history.append(entry)
                if entry["status"] == "updated":
                    config.WEIGHTS.update(entry["after"]["weights"])
                    config.HOME_FIELD = entry["after"]["home_field"]
                r = recmod.compute({p["id"]: p for p in wk_picks}, season, config.LEAN_EDGE, config.PLAY_EDGE, config.STRONG_EDGE)
                weeks_out.append({"season": season, "week": week, "season_type": stype, "n": len(wk_picks),
                                  "ats": r["ats"], "ats_play": r["ats_play"], "ats_strong": r["ats_strong"], "su": r["su"],
                                  "ou": ou_record(wk_picks), "ou_play": ou_record(wk_picks, play=True),
                                  "tune": entry["status"], "weights": dict(config.WEIGHTS), "home_field": config.HOME_FIELD})
                log(f"{season} {stype[:4]} wk {week:>2}: {len(wk_picks)} picks, ATS {r['ats']['W']}-{r['ats']['L']}"
                    f"{'-' + str(r['ats']['P']) if r['ats'].get('P') else ''}, plays {r['ats_play']['W']}-{r['ats_play']['L']}"
                    f" | tune {entry['status']} -> {config.WEIGHTS} HFA {config.HOME_FIELD}")
        final = {"weights": dict(config.WEIGHTS), "home_field": config.HOME_FIELD}
    finally:
        config.WEIGHTS.clear()
        config.WEIGHTS.update(saved[0])
        config.HOME_FIELD = saved[1]

    summary = {}
    for season in seasons:
        r = recmod.compute(picks, season, config.LEAN_EDGE, config.PLAY_EDGE, config.STRONG_EDGE)
        summary[str(season)] = {k: r[k] for k in ("su", "ats", "ats_lean", "ats_play", "ats_strong", "ats_by_conf")}
        sp = [p for p in picks.values() if p["season"] == season]
        summary[str(season)].update({"ou": ou_record(sp), "ou_play": ou_record(sp, play=True)})
    allr = recmod.compute(picks, None, config.LEAN_EDGE, config.PLAY_EDGE, config.STRONG_EDGE)
    summary["all"] = {k: allr[k] for k in ("su", "ats", "ats_lean", "ats_play", "ats_strong", "ats_by_conf")}
    summary["all"].update({"ou": ou_record(list(picks.values())), "ou_play": ou_record(list(picks.values()), play=True)})
    return {"seasons": list(seasons), "picks": picks, "history": history, "weeks": weeks_out,
            "summary": summary, "final": final, "start": {"weights": weights, "home_field": hfa},
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}


def ou_record(items, play=False):
    xs = [p["result"].get("total") for p in items if p.get("total_pick") and (not play or p["total_pick"]["play"])]
    w, l_, pu = xs.count("W"), xs.count("L"), xs.count("P")
    return {"W": w, "L": l_, "P": pu, "pct": round(w / (w + l_), 3) if w + l_ else None}


def site_doc(res):
    """Compact form for the site: per-season/week records and every pick."""
    keep = ("id", "season", "week", "season_type", "start", "home", "away", "home_abbr", "away_abbr", "line",
            "model_spread", "ats_team", "ats_line", "edge", "tier", "cover_prob", "confidence", "conf_label",
            "su_team", "home_qb", "away_qb", "model_total", "total_pick")
    return {
        "sport": "nfl", "generated_at": res["generated_at"], "seasons": res["seasons"],
        "summary": {str(k): v for k, v in res["summary"].items()}, "weeks": res["weeks"],
        "start": res["start"], "final": res["final"],
        "picks": [dict({k: p.get(k) for k in keep}, result={"ats": p["result"]["ats"], "su": p["result"]["su"], "total": p["result"].get("total"),
                                                             "home_pts": p["result"]["home_pts"], "away_pts": p["result"]["away_pts"]})
                  for p in sorted(res["picks"].values(), key=lambda p: (p["season"], _wk(p["season_type"], p["week"]), p["start"] or ""))],
        "method": "Walk-forward: each week rebuilt only from data available before kickoff (play-by-play, Elo, QB "
                  "values, ATS trends from earlier games), priced against nflverse's closing line, then the weekly "
                  "tuner runs on that season's graded picks.",
    }


def apply(res, now=None, log=print):
    """Fold the backtest into the live model: current-season picks the live log
    doesn't have yet, and the tuned weights/home field with their history."""
    now = now or datetime.now(timezone.utc)
    season = pipeline.season_for(now)
    doc = store.load(pipeline.PICKS_PATH, {"picks": {}})
    added = 0
    for gid, p in res["picks"].items():
        if p["season"] == season and gid not in doc["picks"]:
            doc["picks"][gid] = p
            added += 1
    store.save(pipeline.PICKS_PATH, doc)
    w = store.load(pipeline.weights_path(), {"history": []})
    live_hist = [h for h in (w.get("history") or []) if not h.get("backtest")]
    w.update({**res["final"], "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "games": sum(1 for p in res["picks"].values() if p["season"] == season),
              "source": f"backtest {res['seasons'][0]}–{res['seasons'][-1]} walk-forward"})
    w["history"] = (res["history"] + live_hist)[-60:]
    last = res["seasons"][-1]
    if last == season:
        a = next((h["anchor"] for h in res["history"] if h.get("season") == season and h.get("anchor")), None)
        a = a or next((h["before"] for h in res["history"] if h.get("season") == season), res["final"])
        w["anchor"] = {"season": season, "weights": a["weights"], "home_field": a["home_field"]}
    store.save(pipeline.weights_path(), w)
    log(f"Applied backtest: +{added} {season} picks to the live log; weights {res['final']['weights']}, "
        f"HFA {res['final']['home_field']}")
    return added


def save(res):
    slim = ("id", "season", "week", "season_type", "home_abbr", "away_abbr", "line", "model_spread", "diffs",
            "hfa_flag", "offset", "edge", "ats_side", "cover_prob", "confidence", "result")
    store.save(backtest_path(), {k: v for k, v in res.items() if k != "picks"} | {
        "n_picks": len(res["picks"]),
        "picks": [{k: p.get(k) for k in slim} for p in res["picks"].values()]}, compact=True)
    store.save(site_backtest_path(), site_doc(res), compact=True)
