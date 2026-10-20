"""Walk-forward NBA backtest: what the model would have picked each day.

For every game day the model is rebuilt from games finished before that day
and nothing later: ratings (efficiency, points, pace) with last season's
regressed fit as the prior, Elo from the sequential run, player values from
earlier box scores. Injuries are the players who actually sat out (what the
injury report shows at tip-off). Each game is priced against ESPN's closing
DraftKings line, and the weekly tuner runs every Monday on that season's
graded picks, exactly as the live job does.

    python -m nbamodel backtest [--seasons 2026] [--apply]
"""
from datetime import date, datetime, timezone

from cfbmodel import store
from sportscommon import record as recmod
from sportscommon import totals
from sportscommon import tuning as tunemod

from . import config, data, espn, model, players, pipeline, ratings


def backtest_path():
    return config.DATA_DIR / "backtest.json"


def site_backtest_path():
    return config.SITE_DATA_DIR / "backtest.json"


def load_seasons(seasons, today, log=print, fetcher=None):
    """Season caches for the backtest seasons and the one before each (box scores included)."""
    docs = {}
    for s in range(min(seasons) - 1, max(seasons) + 1):
        docs[s] = data.update_season(s, today, log=log, fetcher=fetcher)
    return docs


def _closing(g):
    c = g.get("close") or {}
    o = g.get("odds") or {}
    spread = c.get("spread") if c.get("spread") is not None else o.get("spread")
    total = c.get("total") if c.get("total") is not None else o.get("total")
    return spread, total, c or o


def run(seasons, docs=None, now=None, log=print, start_weights=None, start_hfa=None, fetcher=None):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(espn.ET).date()
    docs = docs or load_seasons(seasons, today, log=log, fetcher=fetcher)
    teams = {}
    for d in docs.values():
        teams.update(d.get("teams") or {})
    name = lambda t: teams.get(t, {}).get("name") or t
    saved = (dict(config.WEIGHTS), config.HOME_COURT)
    weights = dict(start_weights or config.WEIGHTS)
    hfa = config.HOME_COURT if start_hfa is None else start_hfa
    picks, history, weeks_out = {}, [], []
    try:
        config.WEIGHTS.clear()
        config.WEIGHTS.update(weights)
        config.HOME_COURT = hfa
        all_games = [g for d in docs.values() for g in d["games"].values()]
        _, pregame = ratings.elo_run(all_games)
        for season in seasons:
            anchor = {"weights": dict(config.WEIGHTS), "home_field": config.HOME_COURT}
            prev_games = [g for g in docs.get(season - 1, {"games": {}})["games"].values() if g["completed"]]
            prior = model.priors(prev_games, pipeline._season_end(prev_games, date(season - 1, 6, 30)))
            sg = sorted((g for g in docs[season]["games"].values() if g["completed"] and g.get("home_score") is not None),
                        key=lambda g: g["start"] or "")
            dates = model.team_dates(list(docs[season]["games"].values()))
            days = sorted({g["date"] for g in sg})
            week_picks, cur_week = [], None

            def close_week():
                if not week_picks:
                    return
                rows = [(p["diffs"], p["hfa_flag"], (p["result"]["home_pts"] - p["result"]["away_pts"]) - p["offset"])
                        for p in picks.values() if p["season"] == season]
                at = max(p["start"] for p in week_picks)
                entry = tunemod.tune(rows, {k: config.WEIGHTS[k] for k in model.SOURCES}, config.HOME_COURT,
                                     config.MIN_TUNE_GAMES, config.TUNE_PRIOR_GAMES, (0.0, 5.0),
                                     now=datetime.fromisoformat(at.replace("Z", "+00:00")), anchor=anchor)
                entry.update({"season": season, "week": cur_week, "season_type": week_picks[0]["season_type"], "backtest": True})
                history.append(entry)
                if entry["status"] == "updated":
                    config.WEIGHTS.update(entry["after"]["weights"])
                    config.HOME_COURT = entry["after"]["home_field"]
                r = recmod.compute({p["id"]: p for p in week_picks}, season, config.LEAN_EDGE, config.PLAY_EDGE, config.STRONG_EDGE)
                first = min(p["date"] for p in week_picks)
                weeks_out.append({"season": season, "week": cur_week, "season_type": week_picks[0]["season_type"],
                                  "label": f"Week of {date.fromisoformat(first):%b %-d}", "n": len(week_picks),
                                  "ats": r["ats"], "ats_play": r["ats_play"], "ats_strong": r["ats_strong"], "su": r["su"],
                                  "ou": ou_record(week_picks), "ou_play": ou_record(week_picks, play=True),
                                  "tune": entry["status"], "weights": dict(config.WEIGHTS), "home_field": config.HOME_COURT})
                log(f"{season} {weeks_out[-1]['label']}: {len(week_picks)} picks, ATS {r['ats']['W']}-{r['ats']['L']}"
                    f", plays {r['ats_play']['W']}-{r['ats_play']['L']} | tune {entry['status']} -> {config.WEIGHTS} HCA {config.HOME_COURT}")
                week_picks.clear()

            for day in days:
                wk = pipeline.iso_week(day)
                if cur_week is not None and wk != cur_week:
                    close_week()
                cur_week = wk
                done = [g for g in sg if g["date"] < day]
                st = model.build_state(done, prev_games, date.fromisoformat(day), prior)
                for g in (g for g in sg if g["date"] == day):
                    spread, total_line, src = _closing(g)
                    if spread is None:
                        continue
                    h, a = g["home"], g["away"]
                    proj = model.project(g, st, players.expected_from_box(g, h, st["vals"]),
                                         players.expected_from_box(g, a, st["vals"]), dates, pregame.get(g["id"]))
                    dq = min(1.0, min(st["gp"].get(h, 0), st["gp"].get(a, 0)) / 10)
                    fields, detail = model.ats_pick(proj, spread, name, h, a, src.get("home_spread_odds"),
                                                    src.get("away_spread_odds"), None, dq)
                    tp = model.total_pick(proj["total"], total_line, src.get("over_odds"), src.get("under_odds"))
                    hp, ap = g["home_score"], g["away_score"]
                    su_team = name(h if proj["margin"] >= 0 else a)
                    p = {
                        "id": g["id"], "season": season, "week": wk, "season_type": g["season_type"], "date": day,
                        "start": g["start"], "home": name(h), "away": name(a), "home_abbr": h, "away_abbr": a,
                        "neutral": g["neutral"], "line": spread, "book": "DraftKings close (ESPN)", "total_line": total_line,
                        "model_spread": round(-proj["margin"], 1), "model_total": round(proj["total"], 1), "total_pick": tp,
                        "diffs": proj["diffs"], "hfa_flag": proj["hflag"], "offset": proj["offset"],
                        "weights": dict(config.WEIGHTS), "home_field": config.HOME_COURT,
                        "su_team": su_team, "su_wp": round(max(proj["wp"], 1 - proj["wp"]), 3),
                        **fields, "confidence_detail": detail, "backtest": True,
                        "result": {"home_pts": hp, "away_pts": ap, "su": "W" if (hp > ap) == (su_team == name(h)) else "L",
                                   "ats": model.ats_grade(fields["ats_side"], spread, hp, ap),
                                   "total": totals.grade(tp, hp + ap)},
                    }
                    picks[g["id"]] = p
                    week_picks.append(p)
            close_week()
            cur_week = None
        final = {"weights": dict(config.WEIGHTS), "home_field": config.HOME_COURT}
    finally:
        config.WEIGHTS.clear()
        config.WEIGHTS.update(saved[0])
        config.HOME_COURT = saved[1]

    summary = {}
    for key, sel in [(str(s), lambda p, s=s: p["season"] == s) for s in seasons] + [("all", lambda p: True)]:
        sp = {k: p for k, p in picks.items() if sel(p)}
        r = recmod.compute(sp, None, config.LEAN_EDGE, config.PLAY_EDGE, config.STRONG_EDGE)
        summary[key] = {k: r[k] for k in ("su", "ats", "ats_lean", "ats_play", "ats_strong", "ats_by_conf")}
        summary[key].update({"ou": ou_record(sp.values()), "ou_play": ou_record(sp.values(), play=True)})
    return {"seasons": list(seasons), "picks": picks, "history": history, "weeks": weeks_out, "summary": summary,
            "final": final, "start": {"weights": weights, "home_field": hfa},
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}


def ou_record(items, play=False):
    xs = [p["result"].get("total") for p in items if p.get("total_pick") and (not play or p["total_pick"]["play"])]
    w, l_, pu = xs.count("W"), xs.count("L"), xs.count("P")
    return {"W": w, "L": l_, "P": pu, "pct": round(w / (w + l_), 3) if w + l_ else None}


def site_doc(res):
    keep = ("id", "season", "week", "season_type", "start", "home", "away", "home_abbr", "away_abbr", "line",
            "model_spread", "ats_team", "ats_line", "edge", "tier", "cover_prob", "confidence", "conf_label",
            "su_team", "model_total", "total_pick")
    return {
        "sport": "nba", "generated_at": res["generated_at"], "seasons": res["seasons"],
        "summary": res["summary"], "weeks": res["weeks"], "start": res["start"], "final": res["final"],
        "picks": [dict({k: p.get(k) for k in keep}, result={"ats": p["result"]["ats"], "su": p["result"]["su"],
                                                             "total": p["result"].get("total"),
                                                             "home_pts": p["result"]["home_pts"], "away_pts": p["result"]["away_pts"]})
                  for p in sorted(res["picks"].values(), key=lambda p: p["start"] or "")],
        "method": "Walk-forward: each day rebuilt only from games finished before it (efficiency, points, pace, Elo, player "
                  "values), with the players who actually sat out as the injury report, priced against ESPN's closing "
                  "DraftKings line; the weekly tuner runs every Monday on that season's graded picks.",
    }


def apply(res, now=None, log=print):
    """Adopt the backtest's tuned weights/home court (and anchor) for the live model."""
    now = now or datetime.now(timezone.utc)
    w = store.load(pipeline.weights_path(), {"history": []})
    live_hist = [h for h in (w.get("history") or []) if not h.get("backtest")]
    w.update({**res["final"], "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "games": 0,
              "source": f"backtest {res['seasons'][0]}–{res['seasons'][-1]} walk-forward"})
    w["history"] = (res["history"] + live_hist)[-60:]
    season = config.season_for(now.astimezone(espn.ET).date())
    # this season starts from the backtest's end-of-season weights
    w["anchor"] = {"season": season, "weights": dict(res["final"]["weights"]), "home_field": res["final"]["home_field"]}
    store.save(pipeline.weights_path(), w)
    log(f"Applied NBA backtest: weights {res['final']['weights']}, home court {res['final']['home_field']}")


def save(res):
    slim = ("id", "season", "week", "season_type", "home_abbr", "away_abbr", "line", "model_spread", "diffs",
            "hfa_flag", "offset", "edge", "ats_side", "cover_prob", "confidence", "result")
    store.save(backtest_path(), {k: v for k, v in res.items() if k != "picks"} | {
        "n_picks": len(res["picks"]), "picks": [{k: p.get(k) for k in slim} for p in res["picks"].values()]}, compact=True)
    store.save(site_backtest_path(), site_doc(res), compact=True)
