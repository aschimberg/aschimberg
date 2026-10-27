"""NFL refresh / grade / tune pipeline."""
from datetime import datetime, timedelta, timezone

from cfbmodel import ats, store
from cfbmodel.model import grade as ats_grade
from sportscommon import clv, news, oddsapi, totals
from sportscommon import props as sprops
from sportscommon import confidence as conf
from sportscommon import record as recmod
from sportscommon import tuning as tunemod
from sportscommon.odds import american_to_decimal

from . import props as nflprops, starters, config, data, elo as elomod, margins, qb as qbmod, ratings, teams

PICKS_PATH = config.DATA_DIR / "picks.json"
LINE_HISTORY_PATH = config.DATA_DIR / "line_history.json"
SOURCES = ("epa", "elo")


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def season_for(now):
    return now.year if now.month >= 3 else now.year - 1


def weights_path():
    return config.DATA_DIR / "model_weights.json"


def apply_tuning():
    doc = store.load(weights_path(), None)
    if not doc or not config.TUNING:
        return None
    config.WEIGHTS.update({k: float(v) for k, v in doc["weights"].items() if k in config.WEIGHTS})
    config.HOME_FIELD = float(doc["home_field"])
    return doc


def tier(edge):
    if edge >= config.STRONG_EDGE:
        return "strong"
    if edge >= config.PLAY_EDGE:
        return "play"
    if edge >= config.LEAN_EDGE:
        return "lean"
    return "pass"


def _week_key(g):
    return (g["season_type"] != "regular", g["week"])


def current_slate(games):
    """Earliest week with unplayed games. Once only Monday/late games remain
    (2 or fewer), the following week joins the board so its picks get logged."""
    pending = [g for g in games if not g["completed"]]
    if pending:
        key = min(_week_key(g) for g in pending)
    elif games:
        key = max(_week_key(g) for g in games)
    else:
        return None, []
    slate = [g for g in games if _week_key(g) == key]
    if sum(1 for g in slate if not g["completed"]) <= 2:
        later = sorted({_week_key(g) for g in pending if _week_key(g) > key})
        if later:
            slate += [g for g in games if _week_key(g) == later[0]]
    return key, slate


def _label_one(g):
    return f"Week {g['week']}" if g["season_type"] == "regular" else data.POST_WEEK_LABEL.get(g["game_type"], "Playoffs")


def slate_label(slate):
    labels = []
    for g in sorted(slate, key=_week_key):
        lbl = _label_one(g)
        if lbl not in labels:
            labels.append(lbl)
    if len(labels) == 2 and all(l.startswith("Week ") for l in labels):
        return f"Weeks {labels[0][5:]}–{labels[1][5:]}"
    return " & ".join(labels)


def _history_record(g):
    return {"id": g["id"], "season": g["season"], "week": g["week"], "season_type": g["season_type"],
            "start": g["start"], "neutral": g["neutral"], "conference_game": g["div_game"],
            "home": g["home"], "away": g["away"], "home_pts": g["home_pts"], "away_pts": g["away_pts"],
            "completed": g["completed"], "spread": g["spread"]}


def _team_records(games):
    rec = {}
    for g in games:
        if not g["completed"]:
            continue
        for t, mine, theirs in ((g["home"], g["home_pts"], g["away_pts"]), (g["away"], g["away_pts"], g["home_pts"])):
            r = rec.setdefault(t, [0, 0, 0])
            r[0 if mine > theirs else 1 if mine < theirs else 2] += 1
    return {t: (f"{w}-{l}" + (f"-{tie}" if tie else "")) for t, (w, l, tie) in rec.items()}


def _relabel(trend_list):
    for t in trend_list:
        if t["key"] == "conf":
            t["label"] = "Division games"
    return trend_list


def margin_parts(g, fit, pregame, qb_values, baselines, replacement):
    """Per-source margins (diffs), QB/rest offset and home flag for one game."""
    h, a = g["home"], g["away"]
    hflag = 0 if g["neutral"] else 1
    epa_m = (ratings.team_net(fit, h) - ratings.team_net(fit, a)) * config.PLAYS_PER_GAME
    eh, ea = pregame.get(g["id"], (elomod.BASE, elomod.BASE))
    elo_m = (eh - ea) / config.ELO_PER_POINT
    qb_h, _ = qbmod.adjustment(h, g["home_qb_id"], qb_values, baselines, replacement)
    qb_a, _ = qbmod.adjustment(a, g["away_qb_id"], qb_values, baselines, replacement)
    rest = 0.0
    if g["home_rest"] is not None and g["away_rest"] is not None:
        rest = config.REST_PER_DAY * max(-7, min(7, g["home_rest"] - g["away_rest"]))
    offset = (qb_h - qb_a) + rest
    return {"epa": round(epa_m, 2), "elo": round(elo_m, 2)}, offset, hflag, qb_h, qb_a


def project_total(fit, h, a, league_ppg, margin):
    """Projected combined points: league scoring plus each offense vs the other defense (EPA x plays)."""
    off_h = fit["off"].get(h, 0) if fit else 0
    off_a = fit["off"].get(a, 0) if fit else 0
    def_h = fit["def"].get(h, 0) if fit else 0
    def_a = fit["def"].get(a, 0) if fit else 0
    total = 2 * league_ppg + (off_h - def_a + off_a - def_h) * config.PLAYS_PER_GAME
    return max(total, abs(margin) + 10)


def total_pick(model_total, line_total):
    return totals.make_pick(model_total, line_total, config.TOTAL_SD, config.TOTAL_LEAN_EDGE,
                            config.TOTAL_PLAY_EDGE, config.TOTAL_STRONG_EDGE)


def ats_pick(g, margin, dist, diffs, offset, hflag, s, move, trend_block, dq):
    """ATS side, edge, tier, cover probability, EV and confidence vs home spread s."""
    hc, push, ac = margins.cover_probs(dist, s)
    # side follows the projected margin vs the line (the edge); cover_prob prices it
    side = "home" if margin + s >= 0 else "away"
    sign = 1 if side == "home" else -1
    edge = abs(margin + s)
    p_cover = hc if side == "home" else ac
    odds = (g["home_spread_odds"] if side == "home" else g["away_spread_odds"]) or -110
    p_lose = 1 - p_cover - push
    pick = {
        "ats_side": side, "ats_team": teams.name(g[side]), "ats_line": s if side == "home" else -s,
        "edge": round(edge, 1), "tier": tier(edge),
        "cover_prob": round(p_cover / (p_cover + p_lose), 3) if p_cover + p_lose else None,
        # profit per unit staked; a push returns the stake
        "ev": round(p_cover * (american_to_decimal(odds) - 1) - p_lose, 3),
    }
    covers = {k: sign * (diffs[k] + config.HOME_FIELD * hflag + offset + s) for k in SOURCES}
    conf_detail = conf.score(
        side, covers, config.WEIGHTS, edge / config.STRONG_EDGE, 7.0,
        market=conf.market_component(sign, move, 2.5),
        trends=conf.trend_component(trend_block, side, "away" if side == "home" else "home"),
        data=dq, high=config.CONF_HIGH, medium=config.CONF_MEDIUM,
        extra={"sources": {k: round(diffs[k] + config.HOME_FIELD * hflag + offset, 1) for k in SOURCES},
               "source_names": {"epa": "EPA", "elo": "Elo"}},
    )
    if pick["cover_prob"] is not None and pick["cover_prob"] < 0.5:
        pick["tier"] = "pass"  # the point edge doesn't survive key numbers (e.g. -8.5 vs a 10-pt projection)
    pick["confidence"] = conf_detail["score"]
    pick["conf_label"] = conf_detail["label"]
    return pick, conf_detail


def refresh(games=None, pbp_loader=None, now=None, log=print, espn_loader=None):
    now = now or utcnow()
    tuned = apply_tuning()
    if tuned:
        log(f"NFL tuned weights {config.WEIGHTS}, HFA {config.HOME_FIELD}")
    games = games if games is not None else data.load_games()
    season = season_for(now)
    cur = [g for g in games if g["season"] == season]
    if not cur:
        log(f"NFL: no {season} schedule yet")
        return None
    key, slate = current_slate(cur)
    log(f"NFL {season}: slate {slate_label(slate)} ({len(slate)} games)")

    loader = pbp_loader or (lambda s: data.load_pbp(s, season, log=log))
    pbp = {s: loader(s) for s in (season, season - 1, season - 2)}

    prior_fit = ratings.fit_season(pbp[season - 1]["team_games"], decay=1.0)
    fit = ratings.fit_season(pbp[season]["team_games"], prior=prior_fit)

    elo, pregame = elomod.run(games)

    db_rows = [q for s in (season, season - 1) for q in pbp[s]["qb_games"]]
    tot_db = sum(q["db"] for q in db_rows)
    league_db = sum(q["epa"] for q in db_rows) / tot_db if tot_db else 0.0
    qb_seasons = [pbp[season]["qb_games"], pbp[season - 1]["qb_games"], pbp[season - 2]["qb_games"]]
    qb_values, replacement = qbmod.qb_values(qb_seasons, league_db)
    baselines = qbmod.team_baselines(qb_seasons, qb_values)

    kw = margins.key_weights(games)

    hist = [_history_record(g) for g in games
            if season - config.TREND_SEASONS <= g["season"] <= season and g["completed"]]
    trends = ats.compute_trends(hist)
    rest_map = ats.previous_game_dates([_history_record(g) for g in cur])

    lh = store.load(LINE_HISTORY_PATH, {})
    if lh.get("season") != season:
        lh = {"season": season, "games": {}}

    picks_doc = store.load(PICKS_PATH, {"picks": {}})
    picks = picks_doc["picks"]
    by_id = {g["id"]: g for g in games}
    graded = grade_picks(picks, by_id, kw)
    grade_props(picks, by_id, pbp[season].get("td_scorers") or {}, now)
    pg_all = (pbp[season].get("player_games") or []) + (pbp[season - 1].get("player_games") or [])
    prop_rates = nflprops.league_rates(pg_all)
    prop_acc = {}
    prop_events = None
    clv.backfill(picks, lambda gid, p: (lambda sn: (sn[0][0], sn[0][1]) if sn else None)(lh["games"].get(gid)))

    played = {t: 0 for t in teams.TEAMS}
    for g in cur:
        if g["completed"]:
            played[g["home"]] = played.get(g["home"], 0) + 1
            played[g["away"]] = played.get(g["away"], 0) + 1
    recs = _team_records(cur)
    team_ratings = {t: ratings.team_net(fit, t) * config.PLAYS_PER_GAME for t in teams.TEAMS}
    power_rank = {t: i + 1 for i, t in enumerate(sorted(team_ratings, key=lambda t: -team_ratings[t]))}

    league_ppg = 22.0
    done = [g for g in cur if g["completed"]]
    if done:
        league_ppg = sum(g["home_pts"] + g["away_pts"] for g in done) / (2 * len(done))

    upcoming = [g for g in slate if not g["completed"] and (g["start"] is None or g["start"] > iso(now))]
    espn = None
    if upcoming:
        espn = espn_loader() if espn_loader else starters.fetch_espn(log=log)
        directory = starters.qb_directory(games, db_rows)
        last = starters.last_starters(pbp[season]["qb_games"])
        for g in upcoming:
            for side in ("home", "away"):
                qid, nm, src, note = starters.resolve(g[side], g[f"{side}_qb_id"], g[f"{side}_qb"], espn, directory, last)
                if qid is None and nm and src == "espn":
                    qid = f"new:{nm}"  # no NFL dropbacks yet: rated at replacement level
                g[f"{side}_qb_id"], g[f"{side}_qb"], g[f"{side}_qb_note"] = qid, nm, note
                g[f"{side}_qb_src"] = src

    if upcoming:
        events = oddsapi.get_odds("nfl", "spreads,totals", max_age_hours=config.ODDS_API_MAX_AGE, now=now, log=log)
        for g in upcoming:
            ev = oddsapi.find_event(events, teams.name(g["home"]), teams.name(g["away"]), g["start"])
            sp = oddsapi.spreads(ev) if ev else None
            if not sp:
                continue
            g["spread"] = sp["consensus"]
            g["home_spread_odds"], g["away_spread_odds"] = sp["main"]["home_odds"], sp["main"]["away_odds"]
            g["line_book"] = f"consensus of {sp['n_books']} books"
            g["best"] = {"home": sp["best_home"], "away": sp["best_away"]}
            tot = oddsapi.totals(ev)
            if tot:
                g["total"] = tot["consensus"]

    headlines = []
    if upcoming and news.enabled():
        try:
            info = {t: {"name": teams.name(t), "terms": [teams.name(t).split()[-1]]}
                    for g in upcoming for t in (g["home"], g["away"])}
            headlines = news.collect("nfl", info, now, config.DATA_DIR / "news.json", log=log)
        except Exception as e:  # news is context only; never fail the refresh over it
            log(f"News nfl: skipped ({e})")

    actual = {}
    for q in pbp[season]["qb_games"]:
        k = (q["game_id"], q["team"])
        if k not in actual or q["db"] > actual[k][0]:
            actual[k] = (q["db"], q.get("name"))
    for g in slate:
        if g["completed"]:
            for side in ("home", "away"):
                if (g["id"], g[side]) in actual:
                    g[f"{side}_qb"] = actual[(g["id"], g[side])][1]

    cards = []
    for g in sorted(slate, key=lambda x: (x["start"] or "", x["home"])):
        h, a = g["home"], g["away"]
        diffs, offset, hflag, qb_h, qb_a = margin_parts(g, fit, pregame, qb_values, baselines, replacement)
        margin = sum(config.WEIGHTS[k] * diffs[k] for k in SOURCES) + config.HOME_FIELD * hflag + offset
        dist = margins.pmf(margin, kw)
        wp = margins.win_prob(dist)

        total = project_total(fit, h, a, league_ppg, margin)
        proj = {"margin": round(margin, 1), "spread": round(-margin, 1), "total": round(total, 1),
                "home_pts": round((total + margin) / 2, 1), "away_pts": round((total - margin) / 2, 1),
                "home_wp": round(wp, 3)}

        line = None
        move = None
        if g["spread"] is not None:
            snaps = lh["games"].setdefault(g["id"], [])
            snap = [iso(now), g["spread"], g["total"]]
            if not snaps or snaps[-1][1:] != snap[1:]:
                snaps.append(snap)
            open_spread = snaps[0][1]
            move = round(g["spread"] - open_spread, 1) if open_spread is not None else None
            line = {"spread": g["spread"], "open": open_spread, "total": g["total"], "book": g.get("line_book", "nflverse consensus"),
                    "move": move or None, "history": snaps,
                    "home_odds": g["home_spread_odds"], "away_odds": g["away_spread_odds"]}

        su_team = teams.name(h if margin >= 0 else a)
        pick = {"su_team": su_team, "su_wp": round(wp if margin >= 0 else 1 - wp, 3), "ats_side": None,
                "ats_team": None, "ats_line": None, "edge": None, "tier": None, "cover_prob": None, "ev": None,
                "confidence": None, "conf_label": None, "total_pick": total_pick(total, g["total"])}
        if not g["completed"] and (g["start"] is None or g["start"] > iso(now)):
            wk = g["week"]
            if wk not in prop_acc:
                prop_acc[wk] = (nflprops.player_rates(pbp[season].get("player_games") or [],
                                                      pbp[season - 1].get("player_games") or [], prop_rates, wk),
                                nflprops.team_avg_points(games, season, wk))
            acc_w, (avg_pts, lg_pts) = prop_acc[wk]
            players = (nflprops.team_players(h, proj["home_pts"], espn, acc_w, avg_pts.get(h, lg_pts), wk)
                       + nflprops.team_players(a, proj["away_pts"], espn, acc_w, avg_pts.get(a, lg_pts), wk))
            market = None
            if players and oddsapi.api_key() and g["start"] and g["start"] <= iso(now + timedelta(hours=config.PROPS_HOURS)):
                if prop_events is None:
                    prop_events = oddsapi.events("nfl", log=log) or []
                ev = oddsapi.find_event(prop_events, teams.name(h), teams.name(a), g["start"])
                if ev:
                    market = sprops.parse_market(oddsapi.event_props("nfl", ev, now=now, log=log), oddsapi.PROP_MARKETS["nfl"])
            pick["props"] = sprops.best_bets(sprops.price(players, market, config.PROPS_MODEL_WEIGHT), n=config.PROPS_PER_GAME,
                                             min_ev=config.PROPS_MIN_EV, min_prob=config.PROPS_MIN_PROB,
                                             min_games=config.PROPS_MIN_GAMES, max_ratio=config.PROPS_MAX_RATIO)
        trend_block = {}
        for side in ("home", "away"):
            t = g[side]
            ts = None if line is None else (line["spread"] if side == "home" else -line["spread"])
            trend_block[side] = _relabel(ats.relevant_trends(trends, t, _history_record(g), ts, rest_map.get((t, g["id"]))))
        conf_detail = None
        if line:
            qb_known = bool(g["home_qb_id"] and g["away_qb_id"])
            dq = min(1.0, min(played.get(h, 0), played.get(a, 0)) / 4) * (1.0 if qb_known else 0.7)
            fields, conf_detail = ats_pick(g, margin, dist, diffs, offset, hflag, line["spread"], move, trend_block, dq)
            pick.update(fields)

        start = datetime.fromisoformat(g["start"].replace("Z", "+00:00")) if g["start"] else None
        started = start is not None and start <= now
        if not started and not g["completed"]:
            prev = picks.get(g["id"])
            picks[g["id"]] = {
                "id": g["id"], "season": season, "week": g["week"], "season_type": g["season_type"],
                "start": g["start"], "home": teams.name(h), "away": teams.name(a), "home_abbr": h, "away_abbr": a,
                "neutral": g["neutral"], "line": (line or {}).get("spread"), "book": "nflverse consensus",
                "total_line": g["total"], "model_spread": proj["spread"], "model_total": proj["total"],
                "proj_home": proj["home_pts"], "proj_away": proj["away_pts"],
                "diffs": diffs, "hfa_flag": hflag, "offset": round(offset, 2),
                "weights": dict(config.WEIGHTS), "home_field": config.HOME_FIELD,
                **pick, "confidence_detail": conf_detail, "updated": iso(now), "result": None,
            }
            snaps = lh["games"].get(g["id"]) or []
            clv.carry_bet(picks[g["id"]], prev, iso(now), (line or {}).get("spread"),
                          first_seen=(snaps[0][0], snaps[0][1]) if snaps else None)
        logged = picks.get(g["id"])
        if logged and logged.get("confidence_detail"):
            conf_detail = logged["confidence_detail"]

        extras = []
        for side in ("away", "home"):
            qbn = g[f"{side}_qb"]
            adj = qb_h if side == "home" else qb_a
            if qbn:
                note = g.get(f"{side}_qb_note")
                extras.append(f"{teams.name(g[side]).split()[-1]} QB {qbn}" + (f" ({adj:+.1f})" if abs(adj) >= 0.5 else "")
                              + (f" · {note}" if note else ""))
        b = (g.get("best") or {}).get(pick.get("ats_side") or "")
        if b and not started:
            extras.append(f"Best line: {teams.name(g[pick['ats_side']]).split()[-1]} {'PK' if not b['point'] else format(b['point'], '+g')} "
                          f"({b['price']:+.0f}, {b['book']})")
        if g["home_rest"] is not None and g["away_rest"] is not None and g["home_rest"] != g["away_rest"]:
            extras.append(f"Rest {g['away_rest']}d / {g['home_rest']}d")
        if g["roof"] in ("dome", "closed"):
            extras.append("Indoors")
        elif g["wind"] is not None and g["wind"] >= 15:
            extras.append(f"Wind {g['wind']:.0f} mph")

        def tb(side):
            t = g[side]
            return {"name": teams.name(t), "abbr": t, "conf": teams.division(t), "rank": power_rank.get(t),
                    "rating": round(team_ratings.get(t, 0), 1), "record": recs.get(t, "0-0"), "poll_rank": None,
                    "logo": teams.logo(t), "rest_days": g[f"{side}_rest"]}

        res = {"completed": g["completed"], "home_pts": g["home_pts"], "away_pts": g["away_pts"],
               **((logged or {}).get("result") or {})}
        card_pick = {k: logged.get(k) for k in ("ats_side", "ats_team", "ats_line", "edge", "tier", "su_team", "su_wp",
                                                 "line", "confidence", "conf_label", "cover_prob", "ev", "total_pick", "props")} if logged else dict(pick)
        news_block = None
        if not g["completed"]:
            news_block = {"home": news.for_team(headlines, h), "away": news.for_team(headlines, a),
                          "tone": {"home": news.team_tone(headlines, h), "away": news.team_tone(headlines, a)},
                          "market": news.line_move_note(line, teams.name(h), teams.name(a))}
            if headlines and card_pick.get("props"):
                # copies: the flags are for display and stay out of the pick log
                card_pick["props"] = [dict(b, news=news.player_note(headlines, b["name"], b.get("team")))
                                      for b in card_pick["props"]]
        cards.append({
            "id": g["id"], "start": g["start"], "start_tbd": False, "venue": g["stadium"], "tv": None,
            "neutral": g["neutral"], "conference_game": g["div_game"], "home": tb("home"), "away": tb("away"),
            "line": line, "model": proj,
            "pick": card_pick,
            "confidence": conf_detail, "locked": started or g["completed"], "trends": trend_block, "splits": None,
            "result": res, "extras": extras, "news": news_block,
        })

    store.save(LINE_HISTORY_PATH, lh, compact=True)
    store.save(PICKS_PATH, picks_doc)
    log(f"NFL: {len(cards)} games, {graded} picks graded")
    write_site(now, season, slate, cards, fit, elo, team_ratings, power_rank, recs, trends, picks, qb_values)
    return {"season": season, "cards": cards, "graded": graded}


def grade_props(picks, games_by_id, td_scorers, now):
    """Grade anytime-TD best bets once the game's TDs are in play-by-play (or 3 days after, if none)."""
    n = 0
    for gid, p in picks.items():
        if not p.get("props") or p.get("props_graded"):
            continue
        g = games_by_id.get(gid)
        if not g or not g["completed"]:
            continue
        scorers = td_scorers.get(gid)
        if scorers is None:
            started = datetime.fromisoformat(g["start"].replace("Z", "+00:00")) if g.get("start") else now
            if (now - started).days < 3:
                continue  # play-by-play not posted yet
            scorers = []
        sprops.grade(p["props"], [s[0] for s in scorers], [s[1] for s in scorers if s[1]])
        p["props_graded"] = True
        n += 1
    return n


def grade_picks(picks, games_by_id, kw=None):
    def cover_prob(side, spread, close):
        # priced on the closing market: home margin centered on -close, key-number weighted
        hc, push, ac = margins.cover_probs(margins.pmf(-close, kw or {}), spread)
        return (hc if side == "home" else ac) + 0.5 * push

    n = 0
    for gid, p in picks.items():
        if p.get("result"):
            continue
        g = games_by_id.get(gid)
        if not g or not g["completed"]:
            continue
        hp, ap = g["home_pts"], g["away_pts"]
        winner = p["home"] if hp > ap else p["away"] if ap > hp else None
        res = {"home_pts": hp, "away_pts": ap, "su": "P" if winner is None else ("W" if winner == p["su_team"] else "L")}
        if p.get("ats_side") and p.get("line") is not None:
            res["ats"] = ats_grade(p["ats_side"], p["line"], hp, ap)
        if p.get("total_pick"):
            res["total"] = totals.grade(p["total_pick"], hp + ap)
        p["result"] = res
        clv.finalize_spread(p, g.get("spread"), cover_prob if kw else None)
        n += 1
    return n


def compute_record(picks, season):
    r = recmod.compute(picks, season, config.LEAN_EDGE, config.PLAY_EDGE, config.STRONG_EDGE,
                       total_edges=(config.TOTAL_LEAN_EDGE, config.TOTAL_PLAY_EDGE, config.TOTAL_STRONG_EDGE))
    graded = [p for p in picks.values() if p.get("props_graded") and (season is None or p["season"] == season)]
    r["anytime_td"] = sprops.record(graded)
    for w in r["by_week"]:
        td = sprops.record([p for p in graded if (p["season"], p["season_type"], p["week"]) == (w["season"], w["season_type"], w["week"])])
        w["td"] = {k: td[k] for k in ("games", "hits_per_game", "exp_per_game")}
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
<p>Opponent-adjusted <strong>EPA per play</strong> on offense and defense (nflverse play-by-play), fit by weighted ridge regression each week. Recent games count more ({config.EPA_DECAY:g}× per week of age), garbage time is excluded (win probability outside 5–95%), and ratings are shrunk toward {config.PRIOR_KEEP:.0%} of last season's values so early weeks stay stable. Net EPA/play × {config.PLAYS_PER_GAME:g} plays converts to points.</p>
<h3>Elo</h3>
<p>A 538-style Elo from game results since {config.ELO_START_SEASON} (K={config.ELO_K:g}, margin-of-victory multiplier, ⅓ regression each off-season), 25 Elo points per point of spread.</p>
<h3>Quarterbacks</h3>
<p>Each starter's EPA per dropback (this season, last season at 70%, two seasons ago at 40%), shrunk toward replacement level ({config.QB_PRIOR_DB:g} dropbacks of prior). The starter listed in the nflverse schedule is compared with the QB play the team's ratings were built on; the difference × {config.QB_DB_PER_GAME:g} dropbacks moves the line (capped at ±{config.QB_MAX_ADJ:g}). Backup starts are the biggest single adjustment in the model.</p>
<h3>Projection</h3>
<p>Margin = {w['epa']:.0%} EPA + {w['elo']:.0%} Elo + QB adjustment + {config.HOME_FIELD:.2g} points home field (none at neutral sites) + a small rest term ({config.REST_PER_DAY:g} pts/day, capped; post-2011 rest effects are near zero). Cover probability uses a margin distribution reweighted to real NFL final margins since 2005, so key numbers like 3 and 7 carry their true weight and pushes are priced.</p>
<h3>Picks, confidence and tuning</h3>
<p>Edge = projected margin vs the line. Cover probability prices that edge on a margin distribution with real key-number frequencies (3, 7, 10...); a pick under 50% to cover is a pass whatever its edge. Tiers: lean {config.LEAN_EDGE:g}+, play {config.PLAY_EDGE:g}+, strong {config.STRONG_EDGE:g}+. Confidence (0–100) weighs whether EPA and Elo each cover on their own, edge, line movement, ATS trends, and data quality (games played, known starting QBs). After each week the EPA/Elo weights and home field are refit to this season's results and moved part of the way there.</p>
<p>Data: <a href="https://github.com/nflverse">nflverse</a> (CC BY 4.0) schedules, lines and play-by-play. For entertainment only.</p>"""


def write_site(now, season, slate, cards, fit, elo, team_ratings, power_rank, recs, trends, picks, qb_values):
    out = config.SITE_DATA_DIR
    meta = {
        "sport": "nfl", "sport_label": f"{season} NFL", "generated_at": iso(now), "season": season,
        "week": slate[0]["week"], "season_type": slate[0]["season_type"], "slate_label": slate_label(slate),
        "hero_title": f"{slate_label(slate)} <span>NFL</span> <br>Projections.",
        "splits": {"provider": "none", "source": None}, "poll": None,
        "trend_seasons": [season - config.TREND_SEASONS, season],
        "settings": {"weights": config.WEIGHTS, "home_field": config.HOME_FIELD, "lean_edge": config.LEAN_EDGE,
                     "play_edge": config.PLAY_EDGE, "strong_edge": config.STRONG_EDGE, "conf_high": config.CONF_HIGH, "conf_weights": conf.DEFAULT_WEIGHTS,
                     "conf_medium": config.CONF_MEDIUM, "trend_min_games": 8, "bye_days": 13},
        "tuning": tuning_summary(), "site_url": config.SITE_URL,
        "board_note": "Scores are model projections until final. Lines are the nflverse consensus; open is the first line the model saw. Times are Pacific (PT).", "data_source": '<a href="https://github.com/nflverse">nflverse</a> (CC BY 4.0)',
        "method_html": method_html(),
        "rating_columns": [
            {"key": "rank", "label": "#", "digits": 0, "asc": True},
            {"key": "team", "label": "Team", "text": True},
            {"key": "conference", "label": "Division", "text": True},
            {"key": "record", "label": "W–L", "text": True},
            {"key": "rating", "label": "Net pts", "digits": 1},
            {"key": "off", "label": "Off EPA/play", "digits": 3},
            {"key": "def", "label": "Def EPA/play", "digits": 3},
            {"key": "elo", "label": "Elo", "digits": 0},
            {"key": "qb", "label": "Primary QB", "text": True},
            {"key": "qb_value", "label": "QB EPA/db", "digits": 3},
        ],
        "subtitle": "Opponent-adjusted EPA, QB-adjusted, Elo-blended NFL spreads",
        "ratings_note": "Net pts: opponent-adjusted EPA per play × plays per game, vs an average team on a neutral field. Elo shown raw.",
    }
    store.save(out / "meta.json", meta)
    store.save(out / "slate.json", {"generated_at": iso(now), "games": cards}, compact=True)

    primary = {}
    for q in qb_values.values():
        t = q["team"]
        if t not in primary or q["db"] > primary[t]["db"]:
            primary[t] = q
    rows = []
    for t in sorted(teams.TEAMS, key=lambda t: power_rank[t]):
        rows.append({"team": teams.name(t), "conference": teams.division(t), "rank": power_rank[t],
                     "rating": round(team_ratings[t], 2), "off": round((fit or {}).get("off", {}).get(t, 0), 3),
                     "def": round((fit or {}).get("def", {}).get(t, 0), 3), "elo": round(elo.get(t, 1505)),
                     "qb": (primary.get(t) or {}).get("name"),
                     "qb_value": round((primary.get(t) or {}).get("value", 0), 3) if primary.get(t) else None,
                     "record": recs.get(t, "0-0"), "logo": teams.logo(t), "poll_rank": None})
    store.save(out / "ratings.json", {"generated_at": iso(now), "teams": rows}, compact=True)

    labels = dict(ats.LABELS, conf="Division games", nonconf="Non-division")
    trend_rows = {teams.name(t): {c: ats.summarize(v) for c, v in tv.items()} for t, tv in trends.items() if t in teams.TEAMS}
    store.save(out / "trends.json", {"generated_at": iso(now), "labels": labels, "teams": trend_rows}, compact=True)
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
    season = season_for(now)
    picks = store.load(PICKS_PATH, {"picks": {}})["picks"]
    rows = []
    for p in picks.values():
        r = p.get("result")
        if p.get("season") != season or not r or not p.get("diffs"):
            continue
        rows.append((p["diffs"], p.get("hfa_flag", 1), (r["home_pts"] - r["away_pts"]) - p.get("offset", 0)))
    doc = store.load(weights_path(), {"history": []})
    anchor = tunemod.season_anchor(doc, season, {k: config.WEIGHTS[k] for k in SOURCES}, config.HOME_FIELD)
    entry = tunemod.tune(rows, {k: config.WEIGHTS[k] for k in SOURCES}, config.HOME_FIELD, config.MIN_TUNE_GAMES,
                         config.TUNE_PRIOR_GAMES, (0.0, 4.0), now=now, anchor=anchor)
    entry["season"] = season
    doc.update({**entry["after"], "updated": entry["at"], "games": entry["games"]})
    doc["history"] = (doc.get("history") or [])[-51:] + [entry]
    store.save(weights_path(), doc)
    log(f"NFL tuning: {entry['status']} on {entry['games']} games -> {entry['after']}")
    meta_path = config.SITE_DATA_DIR / "meta.json"
    meta = store.load(meta_path, None)
    if meta is not None:
        meta["tuning"] = tuning_summary()
        store.save(meta_path, meta)
    write_record(now, season, picks)
    return entry, compute_record(picks, season)
