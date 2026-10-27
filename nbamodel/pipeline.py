"""NBA refresh / grade / tune pipeline."""
from datetime import date, datetime, timedelta, timezone

from cfbmodel import store
from sportscommon import clv, oddsapi
from sportscommon import confidence as conf
from sportscommon import record as recmod
from sportscommon import tuning as tunemod
from sportscommon import totals
from sportscommon.odds import fmt_american

from . import config, data, espn, model, players, ratings

PICKS_PATH = config.DATA_DIR / "picks.json"
LINE_HISTORY_PATH = config.DATA_DIR / "line_history.json"
SOURCES = model.SOURCES


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def weights_path():
    return config.DATA_DIR / "model_weights.json"


def apply_tuning():
    doc = store.load(weights_path(), None)
    if not doc or not config.TUNING:
        return None
    config.WEIGHTS.update({k: float(v) for k, v in doc["weights"].items() if k in config.WEIGHTS})
    config.HOME_COURT = float(doc["home_field"])
    return doc


def iso_week(d):
    y, w, _ = date.fromisoformat(d).isocalendar()
    return y * 100 + w


def default_loader(now, log):
    today = now.astimezone(espn.ET).date()
    season = config.season_for(today)
    cur = data.update_season(season, today, log=log)
    prev = data.update_season(season - 1, today, log=log, box_budget=config.BOX_BUDGET)
    try:
        report = espn.parse_injuries(espn.injuries(), data.name_map({**prev["teams"], **cur["teams"]}))
        log(f"NBA injury report: {sum(len(v) for v in report.values())} players listed")
    except Exception as e:
        log(f"NBA injury report unavailable: {e}")
        report = {}
    return {"season": season, "cur": cur, "prev": prev, "injuries": report}


def fill_odds_from_api(games, teams, now, log):
    """The Odds API (if ODDS_API_KEY is set) for slate games ESPN hasn't priced yet."""
    need = [g for g in games if not (g.get("odds") or {}).get("spread") and g["start"] and g["start"] > iso(now)
            and g["start"] <= iso(now + timedelta(hours=30))]
    if not need or not oddsapi.api_key():
        return 0
    events = oddsapi.get_odds("nba", "spreads,totals", max_age_hours=config.ODDS_API_MAX_AGE, now=now, log=log)
    n = 0
    for g in need:
        ev = oddsapi.find_event(events, teams.get(g["home"], {}).get("name", g["home"]),
                                teams.get(g["away"], {}).get("name", g["away"]), g["start"])
        sp = oddsapi.spreads(ev) if ev else None
        if not sp:
            continue
        o = dict(g.get("odds") or {})
        o.update({"spread": sp["consensus"], "home_spread_odds": sp["main"]["home_odds"],
                  "away_spread_odds": sp["main"]["away_odds"], "book": f"consensus of {sp['n_books']} books"})
        tot = oddsapi.totals(ev)
        if tot:
            o["total"] = tot["consensus"]
        g["odds"] = o
        n += 1
    log(f"NBA: {n} of {len(need)} unpriced games filled from The Odds API")
    return n


def _season_end(games, fallback):
    ds = [date.fromisoformat(g["date"]) for g in games if g.get("date") and g.get("completed")]
    return max(ds) if ds else fallback


def refresh(loader=None, now=None, log=print):
    now = now or utcnow()
    if apply_tuning():
        log(f"NBA tuned weights {config.WEIGHTS}, home court {config.HOME_COURT}")
    d = (loader or default_loader)(now, log)
    season = d["season"]
    today = now.astimezone(espn.ET).date()
    teams = {**d["prev"].get("teams", {}), **d["cur"].get("teams", {})}
    prev_games = list(d["prev"]["games"].values())
    cur_games = list(d["cur"]["games"].values())
    done = [g for g in cur_games if g["completed"] and g.get("home_score") is not None]

    prior = model.priors(prev_games, _season_end(prev_games, today))
    elo, _ = ratings.elo_run(prev_games + cur_games)
    st = model.build_state(done, prev_games, today, prior, elo)
    dates = model.team_dates(cur_games)

    slate_days = {(today + timedelta(days=i)).isoformat() for i in range(config.SLATE_DAYS)}
    slate = sorted((g for g in cur_games if g.get("date") in slate_days), key=lambda g: (g["start"] or "", g["home"]))
    fill_odds_from_api(slate, teams, now, log)

    lh = store.load(LINE_HISTORY_PATH, {})
    if lh.get("season") != season:
        lh = {"season": season, "games": {}}
    picks_doc = store.load(PICKS_PATH, {"picks": {}})
    picks = picks_doc["picks"]
    graded = grade_picks(picks, {g["id"]: g for g in cur_games}, lh)

    name = lambda t: teams.get(t, {}).get("name") or t
    recs = _records(cur_games)
    net = {t: config.WEIGHTS["eff"] * ratings.net(st["eff"], t) * st["pace"]["mu"] / 100
           + config.WEIGHTS["pts"] * ratings.net(st["pts"], t)
           + config.WEIGHTS["elo"] * (elo.get(t, 1500) - 1500) / config.ELO_PER_POINT for t in config.TEAMS}
    rank = {t: i + 1 for i, t in enumerate(sorted(net, key=lambda t: -net[t]))}
    report = d.get("injuries") or {}

    cards = []
    for g in slate:
        h, a = g["home"], g["away"]
        exp_h = players.expected_from_report(h, report.get(h), st["vals"])
        exp_a = players.expected_from_report(a, report.get(a), st["vals"])
        proj = model.project(g, st, exp_h, exp_a, dates)
        margin, total = proj["margin"], proj["total"]
        o = g.get("odds") or {}
        started = g["state"] != "pre" or (g["start"] is not None and g["start"] <= iso(now))

        line, move = None, None
        if o.get("spread") is not None:
            snaps = lh["games"].setdefault(g["id"], [])
            snap = [iso(now), o["spread"], o.get("total")]
            if not started and (not snaps or snaps[-1][1:] != snap[1:]):
                snaps.append(snap)
            open_spread = o.get("spread_open")
            if open_spread is None and snaps:
                open_spread = snaps[0][1]
            move = round(o["spread"] - open_spread, 1) if open_spread is not None else None
            line = {"spread": o["spread"], "open": open_spread, "total": o.get("total"), "book": o.get("book", "DraftKings"),
                    "move": move or None, "history": snaps, "home_odds": o.get("home_spread_odds"),
                    "away_odds": o.get("away_spread_odds"), "ml_home": o.get("ml_home"), "ml_away": o.get("ml_away")}

        mp = {"margin": round(margin, 1), "spread": round(-margin, 1), "total": round(total, 1),
              "home_pts": round((total + margin) / 2, 1), "away_pts": round((total - margin) / 2, 1),
              "home_wp": round(proj["wp"], 3)}
        is_early = model.early(st, h, a)
        tp = model.total_pick(total, o.get("total"), o.get("over_odds"), o.get("under_odds"))
        pick = {"su_team": name(h if margin >= 0 else a), "su_wp": round(max(proj["wp"], 1 - proj["wp"]), 3),
                "ats_side": None, "ats_team": None, "ats_line": None, "edge": None, "tier": None, "cover_prob": None,
                "ev": None, "confidence": None, "conf_label": None,
                "total_pick": model.cap_early(tp) if is_early else tp}
        conf_detail = None
        if line:
            dq = min(1.0, min(st["gp"].get(h, 0), st["gp"].get(a, 0)) / 10)
            fields, conf_detail = model.ats_pick(proj, line["spread"], name, h, a, line["home_odds"], line["away_odds"], move, dq)
            pick.update(model.cap_early(fields) if is_early else fields)

        if not started and line:
            prev = picks.get(g["id"])
            picks[g["id"]] = {
                "id": g["id"], "season": season, "week": iso_week(g["date"]), "season_type": g["season_type"],
                "date": g["date"], "start": g["start"], "home": name(h), "away": name(a), "home_abbr": h, "away_abbr": a,
                "neutral": g["neutral"], "line": line["spread"], "book": line["book"], "total_line": line["total"],
                "model_spread": mp["spread"], "model_total": mp["total"], "proj_home": mp["home_pts"], "proj_away": mp["away_pts"],
                "diffs": proj["diffs"], "hfa_flag": proj["hflag"], "offset": proj["offset"],
                "weights": dict(config.WEIGHTS), "home_field": config.HOME_COURT,
                **pick, "confidence_detail": conf_detail, "updated": iso(now), "result": None,
            }
            snaps = lh["games"].get(g["id"]) or []
            clv.carry_bet(picks[g["id"]], prev, iso(now), line["spread"],
                          first_seen=(snaps[0][0], snaps[0][1]) if snaps else None)
        logged = picks.get(g["id"])
        if logged and logged.get("confidence_detail"):
            conf_detail = logged["confidence_detail"]

        extras = []
        if is_early and not started:
            extras.append(f"Early season: ratings are still mostly last season's (roster changes aren't modeled), "
                          f"so picks top out at lean until both teams have played {config.EARLY_GAMES} games")
        for side in ("away", "home"):
            adj, notes = proj["inj"][side]
            if notes:
                who = ", ".join(f"{n} {'out' if p == 0 else f'{round(p * 100)}%'} ({dv:+.1f})" for n, dv, p, _r in notes[:3])
                extras.append(f"{g[side]} injuries: {who} · net {adj:+.1f}")
            if proj["b2b"][side]:
                extras.append(f"{g[side]} on a back-to-back ({-config.B2B_PENALTY:+g})")
        if o.get("ml_home") is not None and o.get("ml_away") is not None:
            extras.append(f"Moneyline {g['away']} {fmt_american(o['ml_away'])} / {g['home']} {fmt_american(o['ml_home'])}"
                          f" · model {g['home']} {round(proj['wp'] * 100)}%")

        def tb(side):
            t = g[side]
            return {"name": name(t), "abbr": t, "conf": config.TEAMS.get(t), "rank": rank.get(t),
                    "rating": round(net.get(t, 0), 1), "record": recs.get(t, "0-0"), "poll_rank": None,
                    "logo": teams.get(t, {}).get("logo")}

        res = {"completed": g["completed"], "home_pts": g.get("home_score"), "away_pts": g.get("away_score"),
               **((logged or {}).get("result") or {})}
        cards.append({
            "id": g["id"], "start": g["start"], "start_tbd": False, "venue": g.get("venue"),
            "tv": [g["tv"]] if isinstance(g.get("tv"), str) else g.get("tv"),  # the site expects a list
            "neutral": g["neutral"], "conference_game": False, "home": tb("home"), "away": tb("away"),
            "line": line, "model": mp,
            "pick": {k: logged.get(k) for k in ("ats_side", "ats_team", "ats_line", "edge", "tier", "su_team", "su_wp", "line",
                                                 "confidence", "conf_label", "cover_prob", "ev", "fair_line", "total_pick")} if logged else pick,
            "confidence": conf_detail, "locked": started, "trends": None, "splits": None, "result": res, "extras": extras,
            "status_detail": g.get("detail") if g["state"] == "in" else None,
        })

    store.save(LINE_HISTORY_PATH, lh, compact=True)
    store.save(PICKS_PATH, picks_doc)
    log(f"NBA {season}: {len(cards)} games on the board, {graded} picks graded")
    write_site(now, season, cards, st, elo, net, rank, recs, teams, picks)
    return {"season": season, "cards": cards, "graded": graded}


def _records(games):
    rec = {}
    for g in games:
        if not g["completed"] or g.get("home_score") is None:
            continue
        for t, mine, theirs in ((g["home"], g["home_score"], g["away_score"]), (g["away"], g["away_score"], g["home_score"])):
            r = rec.setdefault(t, [0, 0])
            r[0 if mine > theirs else 1] += 1
    return {t: f"{w}-{l_}" for t, (w, l_) in rec.items()}


def close_spread(g, lh):
    """Closing home spread: ESPN pickcenter after the game, else our last pre-tip snapshot."""
    c = g.get("close") or {}
    if c.get("spread") is not None:
        return c["spread"]
    snaps = [s for s in (lh.get("games") or {}).get(g["id"], []) if g.get("start") and s[0] <= g["start"]]
    return snaps[-1][1] if snaps else None


def grade_picks(picks, games_by_id, lh):
    n = 0
    for gid, p in picks.items():
        if p.get("result"):
            continue
        g = games_by_id.get(gid)
        if not g or not g["completed"] or g.get("home_score") is None:
            continue
        hp, ap = g["home_score"], g["away_score"]
        winner = p["home"] if hp > ap else p["away"]
        res = {"home_pts": hp, "away_pts": ap, "su": "W" if winner == p["su_team"] else "L"}
        if p.get("ats_side") and p.get("line") is not None:
            res["ats"] = model.ats_grade(p["ats_side"], p["line"], hp, ap)
        if p.get("total_pick"):
            res["total"] = totals.grade(p["total_pick"], hp + ap)
        p["result"] = res
        clv.finalize_spread(p, close_spread(g, lh))
        n += 1
    return n


def compute_record(picks, season):
    r = recmod.compute(picks, season, config.LEAN_EDGE, config.PLAY_EDGE, config.STRONG_EDGE,
                       total_edges=(config.TOTAL_LEAN_EDGE, config.TOTAL_PLAY_EDGE, config.TOTAL_STRONG_EDGE))
    dates = {}
    for p in picks.values():
        if p.get("date"):
            k = (p["season"], p["season_type"], p["week"])
            dates[k] = min(dates.get(k, p["date"]), p["date"])
    for w in r["by_week"]:
        d = dates.get((w["season"], w["season_type"], w["week"]))
        if d:
            w["label"] = f"Week of {date.fromisoformat(d):%b %-d}"
    return r


def tuning_summary():
    doc = store.load(weights_path(), None)
    base = {"enabled": config.TUNING, "min_games": config.MIN_TUNE_GAMES}
    if not doc:
        return {**base, "status": "not yet tuned"}
    hist = doc.get("history") or []
    return {**base, "updated": doc.get("updated"), "games": doc.get("games"), "weights": doc.get("weights"),
            "home_field": doc.get("home_field"), "last": hist[-1] if hist else None,
            "history": [{"at": h["at"], "games": h["games"], "status": h["status"], "weights": h["after"]["weights"],
                         "home_field": h["after"]["home_field"], "rmse_before": h.get("rmse_before"),
                         "rmse_after": h.get("rmse_after")} for h in hist[-20:]]}


def method_html():
    w = config.WEIGHTS
    return f"""
<h3>Team strength</h3>
<p>Three margin sources, blended {w['eff']:.0%} efficiency / {w['pts']:.0%} points / {w['elo']:.0%} Elo:</p>
<ul>
<li><strong>Efficiency</strong>: opponent-adjusted points per 100 possessions on offense and defense (ridge regression; possessions from ESPN box scores), times the projected pace of the game.</li>
<li><strong>Points</strong>: the same fit on points per game.</li>
<li><strong>Elo</strong>: a 538-style Elo with a margin-of-victory multiplier, {config.ELO_PER_POINT:g} Elo points per point of spread.</li>
</ul>
<p>Recent games count more ({config.DECAY_PER_DAY:g}× per day). Each season starts from {config.PRIOR_KEEP:.0%} of last season's ratings, worth {config.RIDGE_GAMES:g} games of evidence, so October numbers don't swing on two results.</p>
<h3>Injuries</h3>
<p>Each player's value is his Game Score per minute (this season, plus last season at half weight, shrunk toward replacement level) above replacement, times his usual minutes, converted to points ({config.PLAYER_K:g} points of margin per Game Score point). Team ratings already reflect whoever has been playing, so the line only moves by the difference between tonight and the last {config.AVAIL_GAMES} games. Out counts fully, Doubtful 80%, Questionable 50%, Day-To-Day 35%. A player out for weeks moves nothing; one hurt yesterday moves it fully. Injury notes on each card show the biggest adjustments.</p>
<h3>Schedule and home court</h3>
<p>Home court {config.HOME_COURT:.2g} points (none at neutral sites). The second night of a back-to-back costs {config.B2B_PENALTY:g} points.</p>
<h3>Totals</h3>
<p>Projected possessions × each offense vs the other defense (efficiency), blended 60/40 with the points fit; half of each team's injury adjustment also comes off the total.</p>
<h3>Picks, confidence and tuning</h3>
<p>Edge = projected margin vs the DraftKings spread (via ESPN). Backtests show the model's gap to the line hasn't predicted results, so bets are priced at a market-anchored line that moves {config.ANCHOR_WEIGHT:.0%} of the way from the line toward the model, at most {config.ANCHOR_MAX_SHIFT:g} points. Tiers come from expected value at that price: lean above 0%, play 1.5%+, strong 3%+. Cover chances use a normal margin distribution (SD {config.MARGIN_SD:g}); over/unders are priced the same way (SD {config.TOTAL_SD:g}). Confidence weighs whether each source covers on its own, the edge, line movement and data quality. Every Monday the source weights and home court are refit to this season's results and moved part of the way there.</p>
<p>Data: ESPN's public NBA API (schedule, scores, box scores, DraftKings lines, injury report). For entertainment only.</p>"""


def write_site(now, season, cards, st, elo, net, rank, recs, teams, picks):
    out = config.SITE_DATA_DIR
    meta = {
        "sport": "nba", "sport_label": f"{config.season_label(season)} NBA", "generated_at": iso(now), "season": season,
        "week": None, "season_type": "regular", "slate_label": "Today & tomorrow",
        "hero_title": "Tonight's <span>NBA</span> <br>Projections.",
        "splits": {"provider": "none", "source": None}, "poll": None, "trend_seasons": None,
        "settings": {"weights": config.WEIGHTS, "home_field": config.HOME_COURT, "lean_edge": config.LEAN_EDGE,
                     "anchor_weight": config.ANCHOR_WEIGHT, "anchor_max_shift": config.ANCHOR_MAX_SHIFT,
                     "play_edge": config.PLAY_EDGE, "strong_edge": config.STRONG_EDGE, "conf_high": config.CONF_HIGH,
                     "conf_weights": conf.DEFAULT_WEIGHTS, "conf_medium": config.CONF_MEDIUM},
        "terms": {"start": "tip-off", "record_week": "Week",
                  "record_note": "Picks lock at the last DraftKings spread before tip-off and are graded against it."},
        "tuning": tuning_summary(), "site_url": config.SITE_URL,
        "board_note": "Scores are model projections until final. Lines are DraftKings via ESPN; open is the opening line. Times are Pacific (PT).",
        "data_source": '<a href="https://www.espn.com/nba/">ESPN</a> public API',
        "method_html": method_html(),
        "rating_columns": [
            {"key": "rank", "label": "#", "digits": 0, "asc": True},
            {"key": "team", "label": "Team", "text": True},
            {"key": "conference", "label": "Conf", "text": True},
            {"key": "record", "label": "W–L", "text": True},
            {"key": "rating", "label": "Net pts", "digits": 1},
            {"key": "ortg", "label": "ORtg", "digits": 1},
            {"key": "drtg", "label": "DRtg", "digits": 1, "asc": True},
            {"key": "pace", "label": "Pace", "digits": 1},
            {"key": "elo", "label": "Elo", "digits": 0},
        ],
        "subtitle": "Efficiency-based, injury-adjusted NBA spreads and totals",
        "ratings_note": "Net pts: projected margin vs an average team on a neutral court (efficiency, points and Elo blend). ORtg/DRtg are opponent-adjusted points per 100 possessions; pace is possessions per 48 minutes.",
    }
    store.save(out / "meta.json", meta)
    store.save(out / "slate.json", {"generated_at": iso(now), "games": cards}, compact=True)
    eff, pace = st["eff"], st["pace"]
    rows = []
    for t in sorted(config.TEAMS, key=lambda t: rank[t]):
        rows.append({"team": teams.get(t, {}).get("name") or t, "abbr": t, "conference": config.TEAMS[t], "rank": rank[t],
                     "rating": round(net[t], 2), "record": recs.get(t, "0-0"),
                     "ortg": round(eff["mu"] + eff["off"].get(t, 0), 1) if eff else None,
                     "drtg": round(eff["mu"] - eff["def"].get(t, 0), 1) if eff else None,
                     "pace": round(pace["mu"] + 2 * pace["team"].get(t, 0), 1), "elo": round(elo.get(t, 1500)),
                     "logo": teams.get(t, {}).get("logo"), "poll_rank": None})
    store.save(out / "ratings.json", {"generated_at": iso(now), "teams": rows}, compact=True)
    write_record(now, season, picks)


LOG_DROP = ("diffs", "confidence_detail", "weights", "home_field", "hfa_flag", "offset")


def write_record(now, season, picks):
    rows = sorted(({k: v for k, v in p.items() if k not in LOG_DROP} for p in picks.values() if p["season"] == season),
                  key=lambda p: p.get("start") or "", reverse=True)
    store.save(config.SITE_DATA_DIR / "record.json",
               {"generated_at": iso(now), "season": season, "season_record": compute_record(picks, season),
                "all_time": compute_record(picks, None), "picks": rows}, compact=True)


def tune(now=None, log=print):
    now = now or utcnow()
    season = config.season_for(now.astimezone(espn.ET).date())
    picks = store.load(PICKS_PATH, {"picks": {}})["picks"]
    rows = []
    for p in picks.values():
        r = p.get("result")
        if p.get("season") != season or not r or not p.get("diffs"):
            continue
        rows.append((p["diffs"], p.get("hfa_flag", 1), (r["home_pts"] - r["away_pts"]) - p.get("offset", 0)))
    doc = store.load(weights_path(), {"history": []})
    anchor = tunemod.season_anchor(doc, season, {k: config.WEIGHTS[k] for k in SOURCES}, config.HOME_COURT)
    entry = tunemod.tune(rows, {k: config.WEIGHTS[k] for k in SOURCES}, config.HOME_COURT, config.MIN_TUNE_GAMES,
                         config.TUNE_PRIOR_GAMES, (0.0, 5.0), now=now, anchor=anchor)
    entry["season"] = season
    doc.update({**entry["after"], "updated": entry["at"], "games": entry["games"]})
    doc["history"] = (doc.get("history") or [])[-51:] + [entry]
    store.save(weights_path(), doc)
    log(f"NBA tuning: {entry['status']} on {entry['games']} games -> {entry['after']}")
    meta_path = config.SITE_DATA_DIR / "meta.json"
    meta = store.load(meta_path, None)
    if meta is not None:
        meta["tuning"] = tuning_summary()
        store.save(meta_path, meta)
    write_record(now, season, picks)
    return entry, compute_record(picks, season)
