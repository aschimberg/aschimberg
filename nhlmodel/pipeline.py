"""NHL refresh / grade / tune pipeline."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from cfbmodel import store
from sportscommon import confidence as conf
from sportscommon import clv, oddsapi
from sportscommon import ridge
from sportscommon.props import blend
from sportscommon import tuning as tunemod
from sportscommon.odds import american_to_decimal, american_to_prob, fmt_american, no_vig, normal_cdf, prob_to_american

from . import api, config, moneypuck, probables, data, elo as elomod, goalies, sim, xg

ET = ZoneInfo("America/New_York")
PICKS_PATH = config.DATA_DIR / "picks.json"
LINE_HISTORY_PATH = config.DATA_DIR / "line_history.json"
SOURCES = ("xg", "mp", "goals", "elo")
NAMES = {"xg": "xG", "mp": "MoneyPuck", "goals": "Goals", "elo": "Elo"}


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def weights_path():
    return config.DATA_DIR / "model_weights.json"


def xg_path():
    return config.DATA_DIR / "xg_model.json"


def apply_tuning():
    doc = store.load(weights_path(), None)
    if not doc or not config.TUNING:
        return None
    config.WEIGHTS.update({k: float(v) for k, v in doc["weights"].items() if k in config.WEIGHTS})
    config.HOME_ICE = float(doc["home_field"])
    return doc


def tier(ev):
    if ev >= config.STRONG_EV:
        return "strong"
    if ev >= config.PLAY_EV:
        return "play"
    if ev >= config.LEAN_EV:
        return "lean"
    return "pass"


# --- data loading -----------------------------------------------------------
def _num(x):
    """'+125', '-110', '6.5', 125 -> float; anything else -> None."""
    try:
        return float(str(x).replace("\u2212", "-").strip())
    except (TypeError, ValueError):
        return None


def parse_partner_odds(doc, now=None):
    """DraftKings (US partner) pre-game markets by game id.

    Tolerant of the feed's variations: id as gameId or id, odds lists on each
    team, string or numeric prices, and market names in any case.
    """
    out = {}
    doc = doc or {}
    for g in doc.get("games", []):
        rec = {}
        for side in ("homeTeam", "awayTeam"):
            key = "home" if side == "homeTeam" else "away"
            for o in (g.get(side) or {}).get("odds", []) or []:
                desc = str(o.get("description") or o.get("type") or "").upper().replace(" ", "_")
                val, q = _num(o.get("value")), str(o.get("qualifier") or "").strip()
                if val is None:
                    continue
                # MONEY_LINE_3_WAY (regulation, with draw) and MONEY_LINE_2_WAY_TNB
                # (tie-no-bet) are different bets; we price the full-game 2-way line.
                if desc in ("MONEY_LINE_2_WAY", "MONEYLINE", "ML"):
                    rec[f"ml_{key}"] = val
                elif desc.startswith("PUCK_LINE") and _num(q) is not None:
                    rec[f"pl_{key}"] = _num(q)
                    rec[f"pl_{key}_odds"] = val
                elif desc.startswith("OVER_UNDER") or desc == "TOTAL":
                    u = q[:1].upper()
                    if u in ("O", "U") and _num(q[1:]) is not None:
                        rec["total"] = _num(q[1:])
                        rec["over_odds" if u == "O" else "under_odds"] = val
        gid = g.get("gameId") or g.get("id")
        start = g.get("startTimeUTC")
        if now is not None and start and datetime.fromisoformat(start.replace("Z", "+00:00")) <= now:
            continue  # the feed lingers on last night's games with live in-game prices
        if rec and gid is not None:
            rec["book"] = (doc.get("bettingPartner") or {}).get("name", "DraftKings")
            out[int(gid)] = rec
    return out


def fill_odds_from_api(odds, sched, teams, today, now, log):
    """The Odds API (if ODDS_API_KEY is set) for slate games the NHL feed hasn't priced yet."""
    days = {(today + timedelta(days=i)).isoformat() for i in range(config.SLATE_DAYS)}
    soon = iso(now + timedelta(hours=14))  # only games close enough to have real lines; saves credits
    need = [g for g in sched.values() if g.get("date") in days and g.get("start") and iso(now) < g["start"] <= soon
            and not ("ml_home" in odds.get(g["id"], {}) and "ml_away" in odds.get(g["id"], {}))]
    if not need or not oddsapi.api_key():
        return odds
    events = oddsapi.get_odds("nhl", "h2h,totals", max_age_hours=config.ODDS_API_MAX_AGE, now=now, log=log)
    n = 0
    for g in need:
        ev = oddsapi.find_event(events, teams.get(g["home"], {}).get("name", g["home"]),
                                teams.get(g["away"], {}).get("name", g["away"]), g["start"])
        ml = oddsapi.moneylines(ev) if ev else None
        if not ml:
            continue
        rec = {"ml_home": float(ml["main"]["home"]), "ml_away": float(ml["main"]["away"]), "book": ml["main"]["book"],
               "best": {"home": ml["best_home"], "away": ml["best_away"]}}
        tot = oddsapi.totals(ev)
        if tot:
            rec.update({"total": tot["main"]["total"], "over_odds": float(tot["main"]["over_odds"]),
                        "under_odds": float(tot["main"]["under_odds"])})
        odds[g["id"]] = {**odds.get(g["id"], {}), **rec}
        n += 1
    log(f"NHL: {n} of {len(need)} unpriced games filled from The Odds API")
    return odds


def default_loader(now, log):
    teams = data.team_list()
    cur_id = config.season_id(now.date())
    prev_id = cur_id - 10001
    prev_doc, _ = data.update_season(prev_id, teams, log=log)
    cur_doc, sched = data.update_season(cur_id, teams, log=log)
    try:
        doc = api.partner_odds("US")
        odds = parse_partner_odds(doc, now)
        feed = (doc or {}).get("games", [])
        full = sum(1 for r in odds.values() if "ml_home" in r and "ml_away" in r)
        log(f"NHL odds ({(doc or {}).get('currentOddsDate')}): {full} pre-game of {len(feed)} feed games priced")
        # keep the last raw feed so a format change can be diagnosed from the repo
        store.save(config.DATA_DIR / "odds_last.json", {"fetched_at": iso(utcnow()), "parsed": {str(k): v for k, v in odds.items()}, "doc": doc})
    except Exception as e:
        log(f"NHL odds unavailable: {e}")
        odds = {}
    today = now.astimezone(ET).date()
    odds = fill_odds_from_api(odds, sched, teams, today, now, log)
    by_name = {info["name"]: t for t, info in teams.items()}
    probs = probables.fetch_probables([today + timedelta(days=i) for i in range(config.SLATE_DAYS)], by_name, log=log)
    return {"teams": teams, "season": cur_id, "prev": list(prev_doc["games"].values()),
            "cur": list(cur_doc["games"].values()), "sched": sched, "odds": odds, "probables": probs,
            "mp": moneypuck.load(cur_id, log=log) if config.MONEYPUCK else None}


# --- model pieces -----------------------------------------------------------
def load_xg_model(shots_iter, retrain=False, log=print):
    model = store.load(xg_path(), None)
    if model and not retrain:
        return model
    shots = [s for s in shots_iter]
    if len(shots) < 2000:
        return model or {"coef": [-2.4] + [0.0] * len(xg.FEATURES), "features": ["intercept"] + xg.FEATURES, "n": 0}
    model = xg.train(shots)
    model["trained_at"] = iso(utcnow())
    store.save(xg_path(), model)
    log(f"NHL xG model trained on {model['n']} shots (logloss {model['logloss']})")
    return model


def game_xg(g, model):
    out = {"home": 0.0, "away": 0.0}
    for s in g["shots"]:
        if s[10] or s[1] > 3:  # regulation, non-empty-net only
            continue
        out["home" if s[0] else "away"] += xg.predict(model, s)
    return out


def fit_strength(games, metric, as_of, prior=None):
    teams = {g["home"] for g in games} | {g["away"] for g in games}
    if prior:
        teams |= set(prior["off"])
    obs = []
    for g in games:
        d = date.fromisoformat(g["date"]) if g.get("date") else as_of
        w = config.DECAY_PER_DAY ** max(0, (as_of - d).days)
        hv, av = metric(g)
        obs.append((g["home"], g["away"], 1, hv, w))
        obs.append((g["away"], g["home"], 0, av, w))
    if not teams:
        return None
    p_off = {k: v * config.PRIOR_KEEP for k, v in (prior or {}).get("off", {}).items()}
    p_def = {k: v * config.PRIOR_KEEP for k, v in (prior or {}).get("def", {}).items()}
    fit = ridge.fit(obs, teams, config.RIDGE_GAMES, p_off, p_def, fit_hfa=True, hfa_lam=50.0)
    if not obs and prior:
        fit["mu"] = prior["mu"]
    return fit


def team_lambda(fit, att, dfn):
    if not fit:
        return 2.9
    return fit["mu"] + fit["off"].get(att, 0.0) - fit["def"].get(dfn, 0.0)


def inv_normal(p):
    lo, hi = -6.0, 6.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if normal_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --- refresh ----------------------------------------------------------------
def refresh(loader=None, now=None, log=print):
    now = now or utcnow()
    tuned = apply_tuning()
    if tuned:
        log(f"NHL tuned weights {config.WEIGHTS}, home ice {config.HOME_ICE}")
    d = (loader or default_loader)(now, log)
    teams, season = d["teams"], d["season"]
    prev = [dict(g, season=season - 10001) for g in d["prev"]]
    cur = [dict(g, season=season) for g in d["cur"]]
    sched, odds = d["sched"], d["odds"]
    today = now.astimezone(ET).date()

    model = load_xg_model((s for g in prev + cur for s in g["shots"]), log=log)
    for g in prev + cur:
        g["xg"] = game_xg(g, model)

    prev_end = max((date.fromisoformat(g["date"]) for g in prev if g.get("date")), default=today)
    xg_prior = fit_strength(prev, lambda g: (g["xg"]["home"], g["xg"]["away"]), prev_end)
    gl_prior = fit_strength(prev, lambda g: (g["reg_home"], g["reg_away"]), prev_end)
    xg_fit = fit_strength(cur, lambda g: (g["xg"]["home"], g["xg"]["away"]), today, xg_prior)
    gl_fit = fit_strength(cur, lambda g: (g["reg_home"], g["reg_away"]), today, gl_prior)

    gnames = goalie_names(prev + cur)
    rates = goalies.gsax([(1.0, cur), (config.GOALIE_LAST_SEASON_WEIGHT, prev)], lambda s: xg.predict(model, s))
    mp = d.get("mp")
    mp_str = moneypuck.team_strength(mp) if mp else {}
    if mp:
        mp_rates = moneypuck.goalie_rates(mp)
        share = config.GOALIE_MP_SHARE
        for gid in set(rates) | set(mp_rates):
            ours, theirs = rates.get(gid), mp_rates.get(gid)
            if ours and theirs:
                rates[gid] = {"rate": (1 - share) * ours["rate"] + share * theirs["rate"], "shots": ours["shots"]}
            elif theirs:
                rates[gid] = theirs
    starts = goalies.team_starts(prev + cur)
    elo, _ = elomod.run(prev + cur)

    fa = {}
    for g in cur or prev:
        for side, opp in (("home", 0), ("away", 1)):
            a = fa.setdefault(g[side], [0, 0])
            a[0] += sum(1 for s in g["shots"] if s[0] == opp and not s[10] and s[1] <= 3)
            a[1] += 1
    league_fa = (sum(v[0] for v in fa.values()) / max(1, sum(v[1] for v in fa.values()))) or 40.0
    fa_rate = {t: (v[0] + 10 * league_fa) / (v[1] + 10) for t, v in fa.items()}

    gp = {}
    played_dates = set()
    for g in cur:
        gp[g["home"]] = gp.get(g["home"], 0) + 1
        gp[g["away"]] = gp.get(g["away"], 0) + 1
    for g in list(sched.values()) + cur:
        if g.get("date"):
            played_dates.add((g["home"], g["date"]))
            played_dates.add((g["away"], g["date"]))

    lh = store.load(LINE_HISTORY_PATH, {})
    if lh.get("season") != season:
        lh = {"season": season, "games": {}}
    picks_doc = store.load(PICKS_PATH, {"picks": {}})
    picks = picks_doc["picks"]
    graded = grade_picks(picks, sched, cur)

    def _first(gid, p):
        sn = lh["games"].get(str(gid)) or []
        return (sn[0][0], sn[0][1] if p["ats_side"] == "home" else sn[0][2]) if sn else None
    clv.backfill(picks, _first, kind="ml")

    slate_days = {(today + timedelta(days=i)).isoformat() for i in range(config.SLATE_DAYS)}
    slate = sorted((g for g in sched.values() if g.get("date") in slate_days), key=lambda g: (g["start"] or "", g["home"]))
    names = {t: v["name"] for t, v in teams.items()}
    cards = []
    for g in slate:
        h, a = g["home"], g["away"]
        hflag = 0 if g.get("neutral") else 1
        lam = {
            "xg": (team_lambda(xg_fit, h, a), team_lambda(xg_fit, a, h)),
            "goals": (team_lambda(gl_fit, h, a), team_lambda(gl_fit, a, h)),
        }
        diffs = {"xg": round(lam["xg"][0] - lam["xg"][1], 3), "goals": round(lam["goals"][0] - lam["goals"][1], 3),
                 "elo": round((elo.get(h, 1500) - elo.get(a, 1500)) / config.ELO_PER_GOAL, 3)}
        # MoneyPuck (flurry/score/venue-adjusted xG); our xG stands in if it's unavailable
        diffs["mp"] = round(sum(mp_str[h]) - sum(mp_str[a]), 3) if h in mp_str and a in mp_str else diffs["xg"]
        total = 0.75 * sum(lam["xg"]) + 0.25 * sum(lam["goals"])

        b2b = {}
        gls = {}
        for side in ("home", "away"):
            t = g[side]
            b2b[side] = goalies.played_on(t, g["date"], played_dates)
            gid, backup = goalies.projected_starter(t, g["date"], starts, b2b[side])
            prob_name = (d.get("probables") or {}).get((g["date"], t))
            confirmed = False
            if prob_name:
                pid = probables.match(prob_name, t, starts, gnames)
                usual, _ = goalies.projected_starter(t, g["date"], starts, False)
                gid, backup, confirmed = pid, pid is not None and pid != usual, True
                if pid is None:
                    gnames[f"new:{prob_name}"] = prob_name
                    gid = f"new:{prob_name}"  # no NHL shots faced yet: league-average
            r = rates.get(gid, {"rate": 0.0})["rate"] if gid else 0.0
            base = goalies.baseline(t, starts, rates)
            fa_t = fa_rate.get(t, league_fa)
            gls[side] = {"id": gid, "backup": backup, "confirmed": confirmed, "gsax_game": round(r * fa_t, 3),
                         "vs_base": round((r - base) * fa_t, 3)}
        w = config.WEIGHTS

        def goalie_effect(side):
            x = gls[side]
            return (w["xg"] + w.get("mp", 0)) * x["gsax_game"] + (w["goals"] + w["elo"]) * x["vs_base"]

        offset = goalie_effect("home") - goalie_effect("away")
        offset += (-config.B2B_PENALTY if b2b["home"] else 0) + (config.B2B_PENALTY if b2b["away"] else 0)
        margin = sum(w[k] * diffs[k] for k in SOURCES) + config.HOME_ICE * hflag + offset
        lh_, la_ = (total + margin) / 2, (total - margin) / 2
        o = odds.get(g["id"], {})
        dist = sim.distribution(lh_, la_)
        mk = sim.markets(dist, o.get("total"))
        p_home = mk["home_win"]
        proj = {"margin": round(margin, 2), "spread": None, "total": round(mk["exp_home"] + mk["exp_away"], 2),
                "home_pts": round(mk["exp_home"], 2), "away_pts": round(mk["exp_away"], 2), "home_wp": round(p_home, 3),
                "fair_home": prob_to_american(p_home), "fair_away": prob_to_american(1 - p_home),
                "home_m15": round(mk["home_m15"], 3), "away_m15": round(mk["away_m15"], 3)}

        started = g["state"] not in ("FUT", "PRE")
        line = None
        pick = {"su_team": names.get(h if p_home >= 0.5 else a, h if p_home >= 0.5 else a),
                "su_wp": round(max(p_home, 1 - p_home), 3), "ats_side": None, "ats_team": None, "edge": None,
                "tier": None, "ev": None, "odds": None, "prob": None, "confidence": None, "conf_label": None,
                "pl_alt": None, "model_prob": None}
        conf_detail = None
        total_pick = None
        if "ml_home" in o and "ml_away" in o and not started:
            snaps = lh["games"].setdefault(str(g["id"]), [])
            snap = [iso(now), o["ml_home"], o["ml_away"], o.get("total")]
            if not snaps or snaps[-1][1:] != snap[1:]:
                snaps.append(snap)
        snaps = lh["games"].get(str(g["id"]), [])
        if "ml_home" in o and "ml_away" in o:
            ph_mkt, pa_mkt = no_vig(american_to_prob(o["ml_home"]), american_to_prob(o["ml_away"]))
            # EV from the model blended with the market (see config.ML_MODEL_WEIGHT)
            pb = blend(p_home, ph_mkt, config.ML_MODEL_WEIGHT)
            ev_h = pb * (american_to_decimal(o["ml_home"]) - 1) - (1 - pb)
            ev_a = (1 - pb) * (american_to_decimal(o["ml_away"]) - 1) - pb
            side = "home" if ev_h >= ev_a else "away"
            sign = 1 if side == "home" else -1
            ev = ev_h if side == "home" else ev_a
            p_side = pb if side == "home" else 1 - pb
            p_model_side = p_home if side == "home" else 1 - p_home
            mkt_side = ph_mkt if side == "home" else pa_mkt
            odds_side = o["ml_home"] if side == "home" else o["ml_away"]
            pick.update({"ats_side": side, "ats_team": names.get(g[side], g[side]), "edge": round(ev * 100, 1),
                         "tier": tier(ev), "ev": round(ev, 4), "odds": odds_side, "prob": round(p_side, 3),
                         "mkt_prob": round(mkt_side, 3), "model_prob": round(p_model_side, 3),
                         "pl_alt": puck_line_alt(side, mkt_side, ev, o, mk)})
            m_mkt = 2.2 * inv_normal(ph_mkt)
            covers = {k: sign * (diffs[k] + config.HOME_ICE * hflag + offset - m_mkt) for k in SOURCES}
            move = None
            if snaps:
                p0 = no_vig(american_to_prob(snaps[0][1]), american_to_prob(snaps[0][2]))[0]
                move = ph_mkt - p0
            market = 0.5 if not move else max(0.0, min(1.0, 0.5 + sign * move / 0.06))
            dq = min(1.0, min(gp.get(h, 0), gp.get(a, 0)) / 10) * (0.9 if (gls["home"]["backup"] or gls["away"]["backup"]) else 1.0)
            conf_detail = conf.score(side, covers, config.WEIGHTS, ev / config.STRONG_EV, 0.6, market=market, trends=0.5,
                                     data=dq, high=config.CONF_HIGH, medium=config.CONF_MEDIUM,
                                     extra={"sources": {k: round(diffs[k] + config.HOME_ICE * hflag + offset, 2) for k in SOURCES},
                                            "source_names": NAMES, "units": "goals"})
            pick["confidence"], pick["conf_label"] = conf_detail["score"], conf_detail["label"]
            line = {"ml_home": o["ml_home"], "ml_away": o["ml_away"], "total": o.get("total"), "book": o.get("book"),
                    "mkt_home": round(ph_mkt, 3), "open_home": snaps[0][1] if snaps else None,
                    "open_away": snaps[0][2] if snaps else None, "move": round(move, 3) if move else None,
                    "pl_home": o.get("pl_home"), "pl_home_odds": o.get("pl_home_odds"),
                    "pl_away": o.get("pl_away"), "pl_away_odds": o.get("pl_away_odds"),
                    "over_odds": o.get("over_odds"), "under_odds": o.get("under_odds")}
            if o.get("total") and o.get("over_odds") and o.get("under_odds"):
                ev_o = mk["over"] * (american_to_decimal(o["over_odds"]) - 1) - mk["under"]
                ev_u = mk["under"] * (american_to_decimal(o["under_odds"]) - 1) - mk["over"]
                tside, tev = ("over", ev_o) if ev_o >= ev_u else ("under", ev_u)
                total_pick = {"side": tside, "line": o["total"], "ev": round(tev, 4),
                              "prob": round(mk[tside] / max(1e-9, mk["over"] + mk["under"]), 3),
                              "odds": o["over_odds"] if tside == "over" else o["under_odds"],
                              "play": tev >= config.TOTAL_MIN_EV, "edge": round(tev * 100, 1),
                              "tier": "strong" if tev >= 2 * config.TOTAL_MIN_EV else "play" if tev >= config.TOTAL_MIN_EV
                              else "lean" if tev >= config.TOTAL_MIN_EV / 2 else "pass"}

        if not started and line:
            prev = picks.get(str(g["id"]))
            picks[str(g["id"])] = {
                "id": g["id"], "season": season, "date": g["date"], "week": iso_week(g["date"]), "season_type": "regular" if g["type"] == 2 else "postseason",
                "start": g["start"], "home": names.get(h, h), "away": names.get(a, a), "home_abbr": h, "away_abbr": a,
                "neutral": bool(g.get("neutral")), "line": None, "model_spread": None,
                "ml_home": o["ml_home"], "ml_away": o["ml_away"], "home_prob": round(p_home, 3),
                "diffs": diffs, "hfa_flag": hflag, "offset": round(offset, 3), "weights": dict(config.WEIGHTS),
                "home_field": config.HOME_ICE, **pick, "total_pick": total_pick,
                "confidence_detail": conf_detail, "updated": iso(now), "result": None,
            }
            snaps = lh["games"].get(str(g["id"])) or []
            side = pick.get("ats_side")
            fs = (snaps[0][0], snaps[0][1] if side == "home" else snaps[0][2]) if snaps and side else None
            clv.carry_bet(picks[str(g["id"])], prev, iso(now), pick.get("odds"), first_seen=fs)
        logged = picks.get(str(g["id"]))
        if logged and logged.get("confidence_detail"):
            conf_detail = logged["confidence_detail"]
        shown = {k: logged.get(k) for k in pick} if logged else pick
        shown["total_pick"] = (logged or {}).get("total_pick", total_pick)

        def gname(side):
            gid = gls[side]["id"]
            return gnames.get(gid, f"#{gid}") if gid else None

        extras = []
        for side in ("away", "home"):
            nm = gname(side)
            if nm:
                tag = (" (backup)" if gls[side]["backup"] else "") + (" · probable" if gls[side]["confirmed"] else " · projected")
                extras.append(f"{g[side]} G {nm}{tag} · GSAx {gls[side]['gsax_game']:+.2f}/gm")
            if b2b[side]:
                extras.append(f"{g[side]} on a back-to-back")
        b = (o.get("best") or {}).get(pick.get("ats_side") or "")
        if b and not started and b["price"] != pick.get("odds"):
            extras.append(f"Best price: {g[pick['ats_side']]} {fmt_american(b['price'])} ({b['book']})")

        res = {"completed": g["state"] in data.DONE_STATES, "home_pts": g.get("home_score"), "away_pts": g.get("away_score"),
               **((logged or {}).get("result") or {})}
        tinfo = lambda t: teams.get(t, {})
        cards.append({
            "id": g["id"], "start": g["start"], "start_tbd": False, "venue": g.get("venue"), "tv": None,
            "neutral": bool(g.get("neutral")), "conference_game": False,
            "home": {"name": names.get(h, h), "abbr": h, "conf": tinfo(h).get("division"), "rank": None,
                     "rating": None, "record": tinfo(h).get("record", ""), "poll_rank": None, "logo": tinfo(h).get("logo")},
            "away": {"name": names.get(a, a), "abbr": a, "conf": tinfo(a).get("division"), "rank": None,
                     "rating": None, "record": tinfo(a).get("record", ""), "poll_rank": None, "logo": tinfo(a).get("logo")},
            "line": line, "model": proj, "pick": shown, "confidence": conf_detail,
            "locked": started, "trends": None, "splits": None, "result": res, "extras": extras,
            "market_rows": market_rows(line, proj, mk, h, a),
            "pick_rows": pick_rows(shown, names),
            "score_decimals": 2,
        })

    store.save(LINE_HISTORY_PATH, lh, compact=True)
    store.save(PICKS_PATH, picks_doc)
    log(f"NHL: {len(cards)} games on the board, {graded} picks graded")
    write_site(now, season, cards, teams, xg_fit, gl_fit, elo, rates, starts, picks, model, gnames, league_fa, mp_str)
    return {"season": season, "cards": cards, "graded": graded}


def iso_week(d):
    y, w, _ = date.fromisoformat(d).isocalendar()
    return y * 100 + w


def goalie_names(games):
    out = {}
    for g in games:
        for k, v in (g.get("goalies") or {}).items():
            out[int(k)] = v
    return out


def market_rows(line, proj, mk, h, a):
    rows = [{"label": "Model win %", "value": f"{a} {round((1 - proj['home_wp']) * 100)}% · {h} {round(proj['home_wp'] * 100)}%"},
            {"label": "Fair ML", "value": f"{a} {fmt_american(proj['fair_away'])} · {h} {fmt_american(proj['fair_home'])}"}]
    if line:
        mv = ""
        if line.get("open_home") is not None and line["open_home"] != line["ml_home"]:
            mv = f" (open {fmt_american(line['open_home'])})"
        rows.insert(0, {"label": f"Moneyline · {line.get('book') or ''}".strip(" ·"),
                        "value": f"{a} {fmt_american(line['ml_away'])} · {h} {fmt_american(line['ml_home'])}{mv}"})
        if line.get("total"):
            rows.append({"label": "Total · proj", "value": f"{line['total']:g} · {proj['total']:.2f}"})
        if line.get("pl_home") is not None:
            fav, p = (h, mk["home_m15"]) if line["pl_home"] < 0 else (a, mk["away_m15"])
            rows.append({"label": "Puck line −1.5", "value": f"{fav} {round(p * 100)}% to cover"})
    else:
        rows.append({"label": "Total · proj", "value": f"{proj['total']:.2f}"})
    return rows


def puck_line_alt(side, mkt_side, ml_ev, o, mk):
    """For an underdog ML pick: the same side at +1.5 instead.

    The score simulation already knows how often the dog loses by exactly one
    (late empty-netters, OT and shootout losses all count), so its +1.5 cover
    chance is priced against the book's puck-line price. 'better' means the
    +1.5 has the higher EV per unit staked.
    """
    if mkt_side >= 0.5 or o.get(f"pl_{side}") != 1.5 or o.get(f"pl_{side}_odds") is None:
        return None
    p = p_model = mk["home_p15"] if side == "home" else mk["away_p15"]
    odds = o[f"pl_{side}_odds"]
    other = "away" if side == "home" else "home"
    if o.get(f"pl_{other}") == -1.5 and o.get(f"pl_{other}_odds") is not None:
        # same treatment as the moneyline, so "better than ML" compares like with like
        p_mkt = no_vig(american_to_prob(odds), american_to_prob(o[f"pl_{other}_odds"]))[0]
        p = blend(p_model, p_mkt, config.ML_MODEL_WEIGHT)
    ev = p * (american_to_decimal(odds) - 1) - (1 - p)
    return {"line": 1.5, "odds": odds, "prob": round(p, 3), "model_prob": round(p_model, 3), "ev": round(ev, 4),
            "better": ev > ml_ev}


def side_margin(p):
    """Goals the picked side won by (negative = lost by), from a graded pick."""
    r = p.get("result") or {}
    if r.get("home_pts") is None or not p.get("ats_side"):
        return None
    m = r["home_pts"] - r["away_pts"]
    return m if p["ats_side"] == "home" else -m


def is_dog(p):
    mp = p.get("mkt_prob")
    if mp is not None:
        return mp < 0.5
    return (p.get("odds") or 0) > 0


def dog_record(graded):
    """Underdog ML picks, the same picks at +1.5, and how many losses were by one goal."""
    dogs = [p for p in graded if p["result"].get("ats") and is_dog(p)]
    ml = _rec(dogs)
    pl = {"W": 0, "L": 0, "P": 0, "units": 0.0, "priced": 0}
    lost_by_one = losses = 0
    better = {"W": 0, "L": 0, "P": 0, "units": 0.0, "risked": 0}
    for p in dogs:
        m = side_margin(p)
        if m is None:
            continue
        cover = m >= -1
        pl["W" if cover else "L"] += 1
        if m < 0:
            losses += 1
            lost_by_one += m == -1
        alt = p.get("pl_alt")
        if alt and alt.get("odds") is not None:
            u = round(american_to_decimal(alt["odds"]) - 1, 3) if cover else -1.0
            pl["units"] += u
            pl["priced"] += 1
            # the bet the card recommended: +1.5 when it had the better EV, else the ML
            pick_u = u if alt.get("better") else p["result"].get("units", 0.0)
            better["W" if (cover if alt.get("better") else m > 0) else "L"] += 1
            better["units"] += pick_u
            better["risked"] += 1
    n = pl["W"] + pl["L"]
    pl["pct"] = round(pl["W"] / n, 3) if n else None
    pl["units"] = round(pl["units"], 2)
    pl["roi"] = round(pl["units"] / pl["priced"], 3) if pl["priced"] else None
    bn = better["W"] + better["L"]
    better["pct"] = round(better["W"] / bn, 3) if bn else None
    better["units"] = round(better["units"], 2)
    better["roi"] = round(better["units"] / better["risked"], 3) if better["risked"] else None
    return {"ml": ml, "pl": pl, "best_of": better, "losses": losses, "lost_by_one": lost_by_one,
            "lost_by_one_rec": {"W": lost_by_one, "L": losses - lost_by_one, "P": 0,
                                "pct": round(lost_by_one / losses, 3) if losses else None}}


def pick_rows(p, names):
    out = []
    if p.get("ats_team"):
        out.append({"text": f"{p['ats_team']} ML {fmt_american(p['odds'])}", "strong": True, "result_key": "ats"})
        out.append({"tier": p["tier"], "text": f"EV {p['edge']:+.1f}%"})
        raw = p.get("model_prob")
        out.append({"text": f"Win {round(p['prob'] * 100)}%" + (f" (model {round(raw * 100)}%, mkt {round(p.get('mkt_prob', 0) * 100)}%)"
                    if raw is not None else f" vs mkt {round(p.get('mkt_prob', 0) * 100)}%"), "dim": True})
        alt = p.get("pl_alt")
        if alt:
            out.append({"text": f"{p['ats_team']} +1.5 {fmt_american(alt['odds'])}", "strong": True, "result_key": "pl"})
            out.append({"tier": "play" if alt["better"] and alt["ev"] > 0 else "pass",
                        "text": f"{round(alt['prob'] * 100)}% · EV {alt['ev'] * 100:+.1f}%" + (" · better than ML" if alt["better"] else "")})
    t = p.get("total_pick")
    if t:
        out.append({"text": f"{t['side'].title()} {t['line']:g} {fmt_american(t['odds'])}", "strong": True, "result_key": "total"})
        out.append({"tier": t.get("tier") or ("play" if t.get("play") else "pass"), "text": f"EV {t['ev'] * 100:+.1f}%"})
    return out


# --- grading / record -------------------------------------------------------
def grade_picks(picks, sched, cur_games):
    finals = {}
    for g in sched.values():
        if g["state"] in data.DONE_STATES and g.get("home_score") is not None:
            finals[str(g["id"])] = (g["home_score"], g["away_score"])
    for g in cur_games:
        finals.setdefault(str(g["id"]), (g["home_score"], g["away_score"]))
    n = 0
    for gid, p in picks.items():
        if p.get("result") or gid not in finals:
            continue
        hs, as_ = finals[gid]
        if hs is None or as_ is None:
            continue
        home_won = hs > as_
        res = {"home_pts": hs, "away_pts": as_, "su": "W" if (p["su_team"] == p["home"]) == home_won else "L"}
        if p.get("ats_side"):
            won = (p["ats_side"] == "home") == home_won
            res["ats"] = "W" if won else "L"
            res["units"] = round(american_to_decimal(p["odds"]) - 1, 3) if won else -1.0
            if p.get("pl_alt"):
                m = (hs - as_) if p["ats_side"] == "home" else (as_ - hs)
                res["pl"] = "W" if m >= -1 else "L"
                res["pl_units"] = round(american_to_decimal(p["pl_alt"]["odds"]) - 1, 3) if m >= -1 else -1.0
        t = p.get("total_pick")
        if t:
            tot = hs + as_
            res["total"] = "P" if tot == t["line"] else ("W" if (tot > t["line"]) == (t["side"] == "over") else "L")
            res["total_units"] = 0.0 if res["total"] == "P" else (round(american_to_decimal(t["odds"]) - 1, 3) if res["total"] == "W" else -1.0)
        p["result"] = res
        clv.finalize_ml(p)
        n += 1
    return n


def _rec(items, key="ats", units_key="units"):
    r = {"W": 0, "L": 0, "P": 0, "units": 0.0, "risked": 0}
    for p in items:
        v = (p.get("result") or {}).get(key)
        if v:
            r[v] += 1
            r["units"] += p["result"].get(units_key, 0.0)
            r["risked"] += 1 if v != "P" else 0
    n = r["W"] + r["L"]
    r["pct"] = round(r["W"] / n, 3) if n else None
    r["units"] = round(r["units"], 2)
    r["roi"] = round(r["units"] / r["risked"], 3) if r["risked"] else None
    return r


def compute_record(picks, season):
    graded = [p for p in picks.values() if p.get("result") and (season is None or p["season"] == season)]
    ml = [p for p in graded if p["result"].get("ats")]
    by = lambda ev: [p for p in ml if (p.get("ev") or 0) >= ev]
    weeks = {}
    for p in graded:
        weeks.setdefault(p["week"], []).append(p)
    by_week = [{"season": season, "season_type": "regular", "week": wk, "label": f"Week of {min(x['date'] for x in items)}",
                "su": _rec(items, "su"), "ats": _rec([x for x in items if x["result"].get("ats")]),
                "ats_plays": _rec([x for x in items if (x.get("ev") or 0) >= config.LEAN_EV and x["result"].get("ats")])}
               for wk, items in sorted(weeks.items())]
    return {"su": _rec(graded, "su"), "ats": _rec(ml), "ats_lean": _rec(by(config.LEAN_EV)),
            "ats_play": _rec(by(config.PLAY_EV)), "ats_strong": _rec(by(config.STRONG_EV)),
            "ats_by_conf": {lbl: _rec([p for p in ml if p.get("conf_label") == lbl]) for lbl in ("High", "Medium", "Low")},
            "totals": _rec([p for p in graded if p["result"].get("total")], "total", "total_units"),
            "totals_play": _rec([p for p in graded if p["result"].get("total") and (p.get("total_pick") or {}).get("play")], "total", "total_units"),
            "dogs": dog_record(graded),
            "by_week": by_week}


def record_tiles(sr):
    u = lambda r: f"{r['units']:+.1f}u" + (f" · ROI {r['roi'] * 100:+.1f}%" if r.get("roi") is not None else "")
    return [
        {"k": "Moneyline · all sides", "rec": sr["ats"], "sub": u(sr["ats"])},
        {"k": f"ML · EV {config.LEAN_EV * 100:g}%+", "rec": sr["ats_lean"], "sub": u(sr["ats_lean"])},
        {"k": f"ML · EV {config.PLAY_EV * 100:g}%+", "rec": sr["ats_play"], "sub": u(sr["ats_play"])},
        {"k": f"ML · EV {config.STRONG_EV * 100:g}%+", "rec": sr["ats_strong"], "sub": u(sr["ats_strong"])},
        {"k": "ML · High confidence", "rec": sr["ats_by_conf"]["High"], "sub": u(sr["ats_by_conf"]["High"])},
        {"k": "Totals · all", "rec": sr["totals"], "sub": u(sr["totals"])},
        {"k": f"Totals · EV {config.TOTAL_MIN_EV * 100:g}%+", "rec": sr.get("totals_play") or sr["totals"], "sub": u(sr.get("totals_play") or sr["totals"])},
    ] + dog_tiles(sr.get("dogs"))


def dog_tiles(d):
    if not d or not (d["ml"]["W"] + d["ml"]["L"]):
        return []
    pl = d["pl"]
    pl_sub = (f"{pl['units']:+.1f}u on {pl['priced']} priced" + (f" · ROI {pl['roi'] * 100:+.1f}%" if pl.get("roi") is not None else "")
              if pl["priced"] else "units start once puck-line prices are logged")
    tiles = [
        {"group": "Underdogs & puck line", "k": "Underdog ML picks", "rec": d["ml"], "sub": f"{d['ml']['units']:+.1f}u"},
        {"group": "Underdogs & puck line", "k": "Same dogs at +1.5", "rec": pl, "sub": pl_sub},
        {"group": "Underdogs & puck line", "k": "Dog losses that covered +1.5", "rec": d["lost_by_one_rec"],
         "sub": f"{d['lost_by_one']} of {d['losses']} losses were by one goal (incl. OT/SO)"},
    ]
    if d["best_of"]["risked"]:
        b = d["best_of"]
        tiles.append({"group": "Underdogs & puck line", "k": "Dogs · better of ML or +1.5", "rec": b,
                      "sub": f"{b['units']:+.1f}u · the bet the card recommended"})
    return tiles


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


def method_html(model):
    w = config.WEIGHTS
    return f"""
<h3>Expected goals</h3>
<p>Our own xG model: a logistic regression on every unblocked shot in the NHL's play-by-play (distance, angle, shot type, rebounds, rush chances, power play / shorthanded). Currently trained on {model.get('n', 0):,} shots. xG measures chance quality, which predicts future scoring better than goals or raw shot counts.</p>
<h3>Team strength</h3>
<p>Opponent-adjusted offense and defense from regulation, non-empty-net xG and goals, fit by weighted ridge regression (recent games weighted {config.DECAY_PER_DAY:g}× per day; shrunk toward {config.PRIOR_KEEP:.0%} of last season). Blended as margin sources: our xG {w['xg']:.0%}, MoneyPuck xG {w.get('mp', 0):.0%}, goals {w['goals']:.0%}, Elo {w['elo']:.0%}.</p>
<h3>MoneyPuck</h3>
<p>Team expected goals and goalie GSAx from <a href="https://moneypuck.com">MoneyPuck.com</a> (data used with credit, non-commercial). Their xG model adds shooter talent and adjusts for flurries, score state and venue. Team xG for/against per game this season is shrunk toward last season by {config.RIDGE_GAMES:g} games and used as a fourth margin source. Each goalie's GSAx is a {config.GOALIE_MP_SHARE:.0%}/{1 - config.GOALIE_MP_SHARE:.0%} blend of MoneyPuck's and ours.</p>
<h3>Goalies</h3>
<p>Goaltending is the most decisive single position. Each goalie's goals saved above expected (GSAx) per unblocked shot over this season and last (60% weight), shrunk by {config.GOALIE_PRIOR_SHOTS:,.0f} shots toward average. The starter is ESPN's listed probable goalie once posted; before that, the team's most frequent recent starter, or the backup when the starter played the night before. xG-based strength gets the full goalie effect; goals and Elo, which already include the usual goalie, get the difference vs the team's baseline.</p>
<h3>Schedule and home ice</h3>
<p>Home ice {config.HOME_ICE:.2g} goals of margin (~54% home win rate). Second night of a back-to-back costs {config.B2B_PENALTY:g} goals (~5% win probability).</p>
<h3>Game simulation</h3>
<p>Regulation goals are Poisson; a team leading by one late scores into the empty net {config.EN_GOAL_LEAD1:.0%} of the time (by two: {config.EN_GOAL_LEAD2:.0%}); ties go to 3-on-3 overtime ({config.OT_DECIDED:.0%} decided, in proportion to scoring rates) and then a shootout. That gives win, puck-line (±1.5) and total probabilities and fair odds.</p>
<h3>Picks</h3>
<p>Moneyline value: model win probability vs DraftKings odds published through the NHL's own API. EV tiers: lean {config.LEAN_EV:.1%}+, play {config.PLAY_EV:.1%}+, strong {config.STRONG_EV:.1%}+. Totals are flagged at {config.TOTAL_MIN_EV:.0%}+ EV. Records are kept in units (1 unit per pick). Confidence weighs whether our xG, MoneyPuck, goals and Elo each see value on the side, EV, line movement and data quality. Weights and home ice are refit weekly to graded games.</p>
<p>Data: NHL public API (schedule, play-by-play, odds), MoneyPuck.com (team xG, goalie GSAx), ESPN (probable goalies). For entertainment only.</p>"""


def write_site(now, season, cards, teams, xg_fit, gl_fit, elo, rates, starts, picks, model, gn, league_fa, mp_str=None):
    mp_str = mp_str or {}
    out = config.SITE_DATA_DIR
    today = now.astimezone(ET).date()
    label = "Today & tomorrow"
    meta = {
        "sport": "nhl", "sport_label": f"{season // 10000}–{str(season % 10000)[2:]} NHL", "generated_at": iso(now),
        "season": season, "week": None, "season_type": "regular", "slate_label": label,
        "hero_title": "Tonight's <span>NHL</span> <br>Projections.",
        "splits": {"provider": "none", "source": None}, "poll": None, "trend_seasons": None,
        "settings": {"weights": config.WEIGHTS, "home_field": config.HOME_ICE, "lean_edge": config.LEAN_EV * 100,
                     "play_edge": config.PLAY_EV * 100, "strong_edge": config.STRONG_EV * 100,
                     "conf_high": config.CONF_HIGH, "conf_weights": conf.DEFAULT_WEIGHTS, "conf_medium": config.CONF_MEDIUM},
        "terms": {"ats": "ML", "edge": "EV %", "pick": "Moneyline pick", "record_week": "Week", "start": "puck drop",
                  "record_note": "Picks lock at the last moneyline before puck drop and are graded straight up and in units (1 unit per pick)."},
        "tuning": tuning_summary(), "site_url": config.SITE_URL,
        "board_note": "Scores are projected goals until final. Moneylines are DraftKings via the NHL API; fair odds are the model's no-vig price. Times are Pacific (PT).", "data_source": '<a href="https://www.nhl.com">NHL public API</a> · <a href="https://moneypuck.com">MoneyPuck.com</a>', "method_html": method_html(model),
        "rating_columns": [
            {"key": "rank", "label": "#", "digits": 0, "asc": True},
            {"key": "team", "label": "Team", "text": True},
            {"key": "conference", "label": "Division", "text": True},
            {"key": "record", "label": "W–L–OTL", "text": True},
            {"key": "rating", "label": "Net goals/gm", "digits": 2},
            {"key": "xgf", "label": "xGF/gm", "digits": 2},
            {"key": "xga", "label": "xGA/gm", "digits": 2, "asc": True},
            {"key": "mp", "label": "MoneyPuck xG±/gm", "digits": 2},
            {"key": "elo", "label": "Elo", "digits": 0},
            {"key": "goalie", "label": "Goalie", "text": True},
            {"key": "gsax", "label": "GSAx/gm", "digits": 2},
        ],
        "subtitle": "Expected-goals, goalie-adjusted NHL moneylines",
        "ratings_note": "Net goals per game vs an average team (xG, MoneyPuck, goals and Elo blend). xGF/xGA are opponent-adjusted expected goals; goalie is the usual starter.",
        "today": today.isoformat(),
    }
    store.save(out / "meta.json", meta)

    rows = []
    for t, info in teams.items():
        xgf = xg_fit["mu"] + xg_fit["off"].get(t, 0) if xg_fit else None
        xga = xg_fit["mu"] - xg_fit["def"].get(t, 0) if xg_fit else None
        net_xg = (xg_fit["off"].get(t, 0) + xg_fit["def"].get(t, 0)) if xg_fit else 0
        net_gl = (gl_fit["off"].get(t, 0) + gl_fit["def"].get(t, 0)) if gl_fit else 0
        net_mp = sum(mp_str[t]) if t in mp_str else net_xg
        w = config.WEIGHTS
        net = w["xg"] * net_xg + w.get("mp", 0) * net_mp + w["goals"] * net_gl + w["elo"] * (elo.get(t, 1500) - 1500) / config.ELO_PER_GOAL
        hist = starts.get(t, [])[-10:]
        top = max(set(g for _, g in hist), key=lambda x: sum(1 for _, y in hist if y == x)) if hist else None
        rows.append({"team": info["name"], "abbr": t, "conference": info.get("division"), "record": info.get("record"),
                     "rating": round(net, 3), "xgf": round(xgf, 2) if xgf is not None else None,
                     "xga": round(xga, 2) if xga is not None else None, "elo": round(elo.get(t, 1500)),
                     "mp": round(sum(mp_str[t]), 2) if t in mp_str else None,
                     "goalie": gn.get(top) if top else None,
                     "gsax": round(rates.get(top, {"rate": 0})["rate"] * league_fa, 2) if top else None,
                     "logo": info.get("logo"), "poll_rank": None})
    rows.sort(key=lambda r: -r["rating"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    by_abbr = {r["abbr"]: r for r in rows}
    for c in cards:
        for side in ("home", "away"):
            r = by_abbr.get(c[side]["abbr"])
            if r:
                c[side]["rank"], c[side]["rating"] = r["rank"], round(r["rating"], 2)
    store.save(out / "slate.json", {"generated_at": iso(now), "games": cards}, compact=True)
    store.save(out / "ratings.json", {"generated_at": iso(now), "teams": rows}, compact=True)
    write_record(now, season, picks)


LOG_DROP = ("diffs", "confidence_detail", "weights", "home_field", "hfa_flag", "offset")


def write_record(now, season, picks):
    rows = sorted(({k: v for k, v in p.items() if k not in LOG_DROP} for p in picks.values() if p["season"] == season),
                  key=lambda p: p.get("start") or "", reverse=True)
    for r in rows:
        r["pick_label"] = f"{r['ats_team']} ML {fmt_american(r['odds'])}" if r.get("ats_team") else None
        r["line_label"] = f"{r['away_abbr']} {fmt_american(r['ml_away'])} / {r['home_abbr']} {fmt_american(r['ml_home'])}"
        r["model_label"] = f"{r['home_abbr']} {round(r['home_prob'] * 100)}%"
    sr = compute_record(picks, season)
    store.save(config.SITE_DATA_DIR / "record.json",
               {"generated_at": iso(now), "season": season, "season_record": sr, "all_time": compute_record(picks, None),
                "picks": rows, "tiles": record_tiles(sr),
                "strip": [{"k": "Moneyline", "rec": sr["ats"], "sub": f"{sr['ats']['units']:+.1f} units"},
                          {"k": f"ML · EV {config.PLAY_EV * 100:g}%+", "rec": sr["ats_play"], "sub": f"{sr['ats_play']['units']:+.1f} units"},
                          {"k": "Totals", "rec": sr["totals"], "sub": f"{sr['totals']['units']:+.1f} units"}]},
               compact=True)


def tune(loader=None, now=None, log=print, retrain_xg=True):
    now = now or utcnow()
    season = config.season_id(now.date())
    picks = store.load(PICKS_PATH, {"picks": {}})["picks"]
    rows = []
    for p in picks.values():
        r = p.get("result")
        if p.get("season") != season or not r or not p.get("diffs"):
            continue
        rows.append((p["diffs"], p.get("hfa_flag", 1), (r["home_pts"] - r["away_pts"]) - p.get("offset", 0)))
    doc = store.load(weights_path(), {"history": []})
    anchor = tunemod.season_anchor(doc, season, {k: config.WEIGHTS[k] for k in SOURCES}, config.HOME_ICE)
    entry = tunemod.tune(rows, {k: config.WEIGHTS[k] for k in SOURCES}, config.HOME_ICE, config.MIN_TUNE_GAMES,
                         config.TUNE_PRIOR_GAMES, (0.0, 0.5), now=now, anchor=anchor)
    entry["season"] = season
    doc.update({**entry["after"], "updated": entry["at"], "games": entry["games"]})
    doc["history"] = (doc.get("history") or [])[-51:] + [entry]
    store.save(weights_path(), doc)
    log(f"NHL tuning: {entry['status']} on {entry['games']} games -> {entry['after']}")
    if retrain_xg:
        cur = data.load_cache(season)
        prev = data.load_cache(season - 10001)
        load_xg_model((s for g in list(prev["games"].values()) + list(cur["games"].values()) for s in g["shots"]),
                      retrain=True, log=log)
    meta_path = config.SITE_DATA_DIR / "meta.json"
    meta = store.load(meta_path, None)
    if meta is not None:
        meta["tuning"] = tuning_summary()
        store.save(meta_path, meta)
    write_record(now, season, picks)
    return entry, compute_record(picks, season)
