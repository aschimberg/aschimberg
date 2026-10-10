"""Refresh, history backfill and grading pipelines."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import ats, config, model, splits, store
from .cfbd import g
from .normalize import closing_spread, lines_by_game, normalize_game, select_line
from .ratings import blend

PICKS_PATH = config.DATA_DIR / "picks.json"
LINE_HISTORY_PATH = config.DATA_DIR / "line_history.json"


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def season_for(now):
    """Aug-Dec -> this year; Jan-Jul -> last year (bowls / offseason)."""
    return now.year if now.month >= 8 else now.year - 1


# --- Calendar ---------------------------------------------------------------
def calendar_weeks(raw):
    weeks = []
    for c in raw or []:
        start = ats.parse_dt(g(c, "startDate") or g(c, "firstGameStart"))
        end = ats.parse_dt(g(c, "endDate"))
        if end is None:
            last = ats.parse_dt(g(c, "lastGameStart"))
            end = last + timedelta(hours=6) if last else None
        if start is None or end is None:
            continue
        weeks.append({"week": g(c, "week"), "season_type": g(c, "seasonType", "regular"), "start": start, "end": end})
    weeks.sort(key=lambda w: w["start"])
    return weeks


def current_slate(weeks, now):
    for w in weeks:
        if w["end"] > now:
            return w
    return weeks[-1] if weeks else None


# --- History ----------------------------------------------------------------
def _keep(gm, spread):
    classes = {gm.get("home_class"), gm.get("away_class")}
    return spread is not None or "fbs" in classes or classes == {None}


def build_history(raw_games, lines_map):
    out = []
    for r in raw_games or []:
        gm = normalize_game(r)
        spread = closing_spread(lines_map.get(gm["id"]))
        if _keep(gm, spread):
            out.append(ats.history_record(gm, spread))
    return out


def fetch_season_history(client, year):
    raw_games = client.games(year, "both")
    lines = {}
    for st in ("regular", "postseason"):
        lines.update(lines_by_game(client.lines(year, st)))
    return build_history(raw_games, lines)


def load_history(client, seasons, log=print):
    games = []
    for yr in seasons:
        path = config.CACHE_DIR / f"history_{yr}.json"
        recs = store.load(path, None)
        if recs is None:
            if client is None:
                continue
            log(f"Backfilling {yr} games + lines from CFBD")
            recs = fetch_season_history(client, yr)
            store.save(path, recs, compact=True)
        games += recs
    return games


def _trim_ratings(sp, fpi, elo):
    return {
        "sp": [
            {"team": g(r, "team"), "conference": g(r, "conference"), "rating": g(r, "rating"),
             "offense": {"rating": g(g(r, "offense", {}), "rating")},
             "defense": {"rating": g(g(r, "defense", {}), "rating")}}
            for r in sp or []
        ],
        "fpi": [{"team": g(r, "team"), "conference": g(r, "conference"), "fpi": g(r, "fpi")} for r in fpi or []],
        "elo": [{"team": g(r, "team"), "conference": g(r, "conference"), "elo": g(r, "elo")} for r in elo or []],
    }


def load_prior_ratings(client, year):
    path = config.CACHE_DIR / f"ratings_{year}.json"
    cached = store.load(path, None)
    if cached is None and client is not None:
        cached = _trim_ratings(client.sp(year), client.fpi(year), client.elo(year))
        store.save(path, cached, compact=True)
    return cached


# --- Picks log --------------------------------------------------------------
def grade_picks(picks, games_by_id):
    graded = 0
    for gid, p in picks.items():
        if p.get("result"):
            continue
        gm = games_by_id.get(int(gid)) if str(gid).isdigit() else games_by_id.get(gid)
        if not gm or not gm.get("completed") or gm.get("home_pts") is None or gm.get("away_pts") is None:
            continue
        hp, ap = gm["home_pts"], gm["away_pts"]
        winner = p["home"] if hp > ap else p["away"]
        res = {"home_pts": hp, "away_pts": ap, "su": "W" if winner == p["su_team"] else "L"}
        if p.get("ats_side") and p.get("line") is not None:
            res["ats"] = model.grade(p["ats_side"], p["line"], hp, ap)
        p["result"] = res
        graded += 1
    return graded


def _rec(items, key):
    r = {"W": 0, "L": 0, "P": 0}
    for p in items:
        v = p["result"].get(key)
        if v:
            r[v] += 1
    n = r["W"] + r["L"]
    r["pct"] = round(r["W"] / n, 3) if n else None
    return r


def compute_record(picks, season=None):
    graded = [p for p in picks.values() if p.get("result") and (season is None or p["season"] == season)]
    ats_graded = [p for p in graded if p["result"].get("ats")]

    def tiered(min_edge):
        return _rec([p for p in ats_graded if (p.get("edge") or 0) >= min_edge], "ats")

    weeks = defaultdict(list)
    for p in graded:
        weeks[(p["season"], p["season_type"], p["week"])].append(p)
    by_week = [
        {"season": s, "season_type": st, "week": w, "su": _rec(items, "su"), "ats": _rec(items, "ats"),
         "ats_plays": _rec([p for p in items if (p.get("edge") or 0) >= config.PLAY_EDGE], "ats")}
        for (s, st, w), items in sorted(weeks.items(), key=lambda kv: (kv[0][0], kv[0][1] != "regular", kv[0][2]))
    ]
    return {
        "su": _rec(graded, "su"),
        "ats": _rec(ats_graded, "ats"),
        "ats_lean": tiered(config.LEAN_EDGE),
        "ats_play": tiered(config.PLAY_EDGE),
        "ats_strong": tiered(config.STRONG_EDGE),
        "by_week": by_week,
    }


def fmt_rec(r, ties=True):
    s = f"{r['W']}-{r['L']}" + (f"-{r['P']}" if ties and r.get("P") else "")
    if r.get("pct") is not None:
        s += f" ({r['pct'] * 100:.1f}%)"
    return s


# --- Refresh ----------------------------------------------------------------
def _team_records(games):
    rec = defaultdict(lambda: [0, 0])
    conf_rec = defaultdict(lambda: [0, 0])
    for gm in games:
        if not gm["completed"] or gm["home_pts"] is None or gm["away_pts"] is None:
            continue
        home_won = gm["home_pts"] > gm["away_pts"]
        rec[gm["home"]][0 if home_won else 1] += 1
        rec[gm["away"]][1 if home_won else 0] += 1
        if gm["conference_game"]:
            conf_rec[gm["home"]][0 if home_won else 1] += 1
            conf_rec[gm["away"]][1 if home_won else 0] += 1
    return {t: {"w": v[0], "l": v[1], "cw": conf_rec[t][0], "cl": conf_rec[t][1]} for t, v in rec.items()}


def _update_line_history(hist, season, slate_games, now):
    if hist.get("season") != season:
        hist = {"season": season, "games": {}}
    for gm, line in slate_games:
        if not line:
            continue
        snaps = hist["games"].setdefault(str(gm["id"]), [])
        snap = [iso(now), line["spread"], line.get("total")]
        if not snaps or snaps[-1][1:] != snap[1:]:
            snaps.append(snap)
    return hist


def refresh(client, now=None, splits_provider=None, log=print):
    now = now or utcnow()
    season = season_for(now)
    weeks = calendar_weeks(client.calendar(season))
    slate = current_slate(weeks, now)
    if slate is None:
        raise RuntimeError(f"No CFBD calendar for {season}")
    log(f"Season {season}, slate: {slate['season_type']} week {slate['week']}")

    fbs = {g(t, "school") for t in client.fbs_teams(season) or []}
    cur_games = [normalize_game(r) for r in client.games(season, "both")]
    raw_lines = client.lines(season, "regular")
    if slate["season_type"] != "regular":
        raw_lines += client.lines(season, slate["season_type"])
    lmap = lines_by_game(raw_lines)

    current = {"sp": client.sp(season), "fpi": client.fpi(season), "elo": client.elo(season)}
    prior = load_prior_ratings(client, season - 1)
    ratings, lg_off = blend(current, prior, fbs)

    hist_seasons = list(range(season - config.TREND_SEASONS, season))
    history = load_history(client, hist_seasons, log=log)
    cur_hist = [ats.history_record(gm, closing_spread(lmap.get(gm["id"]))) for gm in cur_games if gm["completed"]]
    trends = ats.compute_trends(history + cur_hist)

    media = {}
    try:
        for m in client.media(season, slate["week"], slate["season_type"]) or []:
            media.setdefault(g(m, "id"), []).append(g(m, "outlet"))
    except Exception as e:  # media is cosmetic; never fail the refresh on it
        log(f"media lookup skipped: {e}")

    provider = splits_provider or splits.get_provider()
    try:
        split_records = provider.fetch()
    except Exception as e:
        log(f"splits provider '{provider.name}' failed: {e}")
        split_records = []

    slate_games = [
        gm for gm in cur_games
        if gm["week"] == slate["week"] and gm["season_type"] == slate["season_type"]
        and (gm["home"] in fbs or gm["away"] in fbs)
    ]
    slate_games.sort(key=lambda x: (x["start"] or "", x["home"]))
    split_map = splits.match(slate_games, split_records)

    with_lines = [(gm, select_line(lmap.get(gm["id"]))) for gm in slate_games]
    line_hist = _update_line_history(store.load(LINE_HISTORY_PATH, {}), season, with_lines, now)
    store.save(LINE_HISTORY_PATH, line_hist, compact=True)

    rest = ats.previous_game_dates(cur_games)
    team_recs = _team_records(cur_games)
    picks_doc = store.load(PICKS_PATH, {"picks": {}})
    picks = picks_doc["picks"]
    games_by_id = {gm["id"]: gm for gm in cur_games}
    graded = grade_picks(picks, games_by_id)

    cards = []
    for gm, line in with_lines:
        proj = model.project(gm, ratings, lg_off, market_total=(line or {}).get("total"))
        pick = model.make_pick(gm, proj, line)
        start = ats.parse_dt(gm["start"])
        started = start is not None and start <= now

        move = None
        if line:
            snaps = line_hist["games"].get(str(gm["id"]), [])
            open_spread = line.get("spread_open")
            if open_spread is None and snaps:
                open_spread = snaps[0][1]
            line["open"] = open_spread
            if open_spread is not None:
                move = round(line["spread"] - open_spread, 1)
            line["move"] = move
            line["history"] = snaps

        key = str(gm["id"])
        if not started and not gm["completed"]:
            picks[key] = {
                "id": gm["id"], "season": season, "week": gm["week"], "season_type": gm["season_type"],
                "start": gm["start"], "home": gm["home"], "away": gm["away"], "neutral": gm["neutral"],
                "line": (line or {}).get("spread"), "book": (line or {}).get("book"),
                "total_line": (line or {}).get("total"),
                "model_spread": proj["spread"], "model_total": proj["total"],
                "proj_home": proj["home_pts"], "proj_away": proj["away_pts"],
                **pick, "updated": iso(now), "result": None,
            }
        logged = picks.get(key)

        trend_block = {}
        for side in ("home", "away"):
            team = gm[side]
            ts = None
            if line:
                ts = line["spread"] if side == "home" else -line["spread"]
            trend_block[side] = ats.relevant_trends(trends, team, gm, ts, rest.get((team, gm["id"])))

        split_info = None
        if gm["id"] in split_map:
            split_info = splits.analyze(split_map[gm["id"]], move)

        def team_block(side):
            t = gm[side]
            r = ratings.get(t, {})
            tr = team_recs.get(t)
            return {
                "name": t, "conf": gm[f"{side}_conf"], "rank": r.get("rank"), "rating": r.get("rating"),
                "record": f"{tr['w']}-{tr['l']}" if tr else "0-0",
                "rest_days": rest.get((t, gm["id"])),
            }

        cards.append({
            "id": gm["id"], "start": gm["start"], "start_tbd": gm["start_tbd"], "venue": gm["venue"],
            "tv": media.get(gm["id"]), "neutral": gm["neutral"], "conference_game": gm["conference_game"],
            "home": team_block("home"), "away": team_block("away"),
            "line": line, "model": proj,
            "pick": {k: logged[k] for k in ("ats_side", "ats_team", "ats_line", "edge", "tier", "su_team", "su_wp", "line")} if logged else pick,
            "locked": started or gm["completed"],
            "trends": trend_block, "splits": split_info,
            "result": {"completed": gm["completed"], "home_pts": gm["home_pts"], "away_pts": gm["away_pts"],
                       **((logged or {}).get("result") or {})},
        })

    store.save(PICKS_PATH, picks_doc)
    log(f"{len(cards)} games on slate, {graded} picks graded, {client.calls} CFBD calls")

    write_site(
        now=now, season=season, slate=slate, cards=cards, ratings=ratings, team_recs=team_recs,
        trends=trends, fbs=fbs, picks=picks, provider=provider, hist_seasons=hist_seasons,
    )
    return {"season": season, "slate": slate, "cards": cards, "graded": graded}


def write_site(now, season, slate, cards, ratings, team_recs, trends, fbs, picks, provider, hist_seasons):
    out = config.SITE_DATA_DIR
    label = f"Week {slate['week']}" if slate["season_type"] == "regular" else "Bowls & Playoff"
    meta = {
        "generated_at": iso(now), "season": season, "week": slate["week"], "season_type": slate["season_type"],
        "slate_label": label,
        "splits": {"provider": provider.name, "source": provider.source_label},
        "trend_seasons": [hist_seasons[0] if hist_seasons else season, season],
        "settings": {
            "weights": config.WEIGHTS, "home_field": config.HOME_FIELD,
            "prior_regression": config.PRIOR_SEASON_REGRESSION, "margin_sd": config.MARGIN_SD,
            "lean_edge": config.LEAN_EDGE, "play_edge": config.PLAY_EDGE, "strong_edge": config.STRONG_EDGE,
            "sharp_gap": config.SHARP_GAP, "public_bets": config.PUBLIC_BETS,
            "trend_min_games": config.TREND_MIN_GAMES, "bye_days": config.BYE_DAYS,
        },
        "site_url": config.SITE_URL,
    }
    store.save(out / "meta.json", meta)
    store.save(out / "slate.json", {"generated_at": iso(now), "games": cards}, compact=True)

    rows = []
    for r in sorted((r for r in ratings.values() if r["team"] in fbs), key=lambda r: r.get("rank") or 999):
        tr = team_recs.get(r["team"], {"w": 0, "l": 0, "cw": 0, "cl": 0})
        rows.append({**{k: r.get(k) for k in ("team", "conference", "rank", "rating", "sp", "fpi", "elo", "off", "def", "sources")},
                     "record": f"{tr['w']}-{tr['l']}", "conf_record": f"{tr['cw']}-{tr['cl']}"})
    store.save(out / "ratings.json", {"generated_at": iso(now), "teams": rows}, compact=True)

    write_record(now, season, picks)

    trend_rows = {}
    for team in sorted(fbs):
        if team in trends:
            trend_rows[team] = {c: ats.summarize(v) for c, v in trends[team].items()}
    store.save(out / "trends.json", {"generated_at": iso(now), "labels": ats.LABELS, "teams": trend_rows}, compact=True)


def write_record(now, season, picks):
    log_rows = sorted(
        (p for p in picks.values() if p["season"] == season),
        key=lambda p: p.get("start") or "", reverse=True,
    )
    store.save(
        config.SITE_DATA_DIR / "record.json",
        {"generated_at": iso(now), "season": season, "season_record": compute_record(picks, season),
         "all_time": compute_record(picks), "picks": log_rows},
        compact=True,
    )


def grade_only(client, now=None, log=print):
    """Sunday grading: pull final scores and grade logged picks."""
    now = now or utcnow()
    picks_doc = store.load(PICKS_PATH, {"picks": {}})
    picks = picks_doc["picks"]
    seasons = sorted({p["season"] for p in picks.values() if not p.get("result")})
    games_by_id = {}
    for yr in seasons:
        for r in client.games(yr, "both"):
            gm = normalize_game(r)
            games_by_id[gm["id"]] = gm
    graded = grade_picks(picks, games_by_id)
    store.save(PICKS_PATH, picks_doc)
    season = season_for(now)
    write_record(now, season, picks)
    log(f"Graded {graded} picks ({client.calls} CFBD calls)")
    return graded, compute_record(picks, season)


def last_week_summary(picks, season):
    """Most recent graded week's record, for the Sunday recap."""
    rec = compute_record(picks, season)
    return rec["by_week"][-1] if rec["by_week"] else None, rec

