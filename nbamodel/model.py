"""NBA projection core, shared by the live refresh and the walk-forward backtest.

State is built from games finished before a date; project() prices one game.
"""
from datetime import date, timedelta

from cfbmodel.model import grade as ats_grade  # noqa: F401  (re-exported for pipeline/backtest)
from sportscommon import confidence as conf
from sportscommon import totals
from sportscommon.odds import american_to_decimal

from . import config, players, ratings

SOURCES = ("eff", "pts", "elo")
SOURCE_NAMES = {"eff": "Efficiency", "pts": "Points", "elo": "Elo"}


def priors(prev_games, prev_end):
    """Last season's full-season fits (the prior for this season's ratings).

    Regular season only, every game weighted equally: playoff games run about
    10 points lower-scoring and would otherwise dominate a recency-weighted fit.
    """
    reg = [g for g in prev_games if g.get("season_type") == "regular"] or list(prev_games)
    return {"pts": ratings.fit_points(reg, prev_end, decay=1.0), "eff": ratings.fit_eff(reg, prev_end, decay=1.0),
            "pace": ratings.fit_pace(reg, prev_end, decay=1.0)}


def build_state(cur_done, prev_games, as_of, prior, elo=None):
    """cur_done: this season's finished games before as_of. elo: {team: rating} pregame."""
    pts = ratings.fit_points(cur_done, as_of, prior.get("pts"))
    eff = ratings.fit_eff(cur_done, as_of, prior.get("eff"))
    pace = ratings.fit_pace(cur_done, as_of, prior.get("pace"))
    vals, league = players.values(cur_done, prev_games)
    hist = players.team_history(list(prev_games) + list(cur_done))
    gp = {}
    for g in cur_done:
        gp[g["home"]] = gp.get(g["home"], 0) + 1
        gp[g["away"]] = gp.get(g["away"], 0) + 1
    return {"pts": pts, "eff": eff, "pace": pace, "vals": vals, "hist": hist, "elo": elo or {}, "gp": gp,
            "league_gs": league}


def played_yesterday(team, d, dates_by_team):
    return (team, (date.fromisoformat(d) - timedelta(days=1)).isoformat()) in dates_by_team


def team_dates(games):
    out = set()
    for g in games:
        if g.get("date"):
            out.add((g["home"], g["date"]))
            out.add((g["away"], g["date"]))
    return out


def project(g, st, expected_h, expected_a, dates_by_team, elo_pair=None):
    """Margin/total/win probability and the parts behind them for one game."""
    h, a = g["home"], g["away"]
    hflag = 0 if g.get("neutral") else 1
    pace = st["pace"]
    poss = pace["mu"] + pace["team"].get(h, 0.0) + pace["team"].get(a, 0.0)
    eff, pts = st["eff"], st["pts"]
    if eff:
        oh = eff["off"].get(h, 0.0) - eff["def"].get(a, 0.0)
        oa = eff["off"].get(a, 0.0) - eff["def"].get(h, 0.0)
        eff_m = (oh - oa) * poss / 100
        eff_total = poss * (2 * eff["mu"] + oh + oa) / 100
    else:
        eff_m, eff_total = 0.0, None
    if pts:
        ph = pts["off"].get(h, 0.0) - pts["def"].get(a, 0.0)
        pa = pts["off"].get(a, 0.0) - pts["def"].get(h, 0.0)
        pts_m = ph - pa
        pts_total = 2 * pts["mu"] + ph + pa + pts.get("hfa", 0.0) * hflag
    else:
        pts_m, pts_total = 0.0, None
    eh, ea = elo_pair or (st["elo"].get(h, 1500.0), st["elo"].get(a, 1500.0))
    diffs = {"eff": round(eff_m, 2), "pts": round(pts_m, 2), "elo": round((eh - ea) / config.ELO_PER_POINT, 2)}

    inj_h, notes_h = players.adjustment(h, st["vals"], st["hist"], expected_h)
    inj_a, notes_a = players.adjustment(a, st["vals"], st["hist"], expected_a)
    b2b_h = played_yesterday(h, g["date"], dates_by_team)
    b2b_a = played_yesterday(a, g["date"], dates_by_team)
    rest = (-config.B2B_PENALTY if b2b_h else 0.0) + (config.B2B_PENALTY if b2b_a else 0.0)
    offset = inj_h - inj_a + rest
    w = config.WEIGHTS
    margin = sum(w[k] * diffs[k] for k in SOURCES) + config.HOME_COURT * hflag + offset
    if eff_total and pts_total:
        total = 0.6 * eff_total + 0.4 * pts_total
    else:
        total = eff_total or pts_total or 2 * 112.0
    total += config.TOTAL_INJURY_SHARE * (inj_h + inj_a)
    wp = ratings.normal_cdf(margin / config.MARGIN_SD)
    return {"diffs": diffs, "offset": round(offset, 2), "hflag": hflag, "margin": margin, "total": total, "wp": wp,
            "poss": poss, "inj": {"home": (inj_h, notes_h), "away": (inj_a, notes_a)},
            "b2b": {"home": b2b_h, "away": b2b_a}}


def tier(edge):
    if edge >= config.STRONG_EDGE:
        return "strong"
    if edge >= config.PLAY_EDGE:
        return "play"
    if edge >= config.LEAN_EDGE:
        return "lean"
    return "pass"


def early(st, h, a):
    """Too few games this season for the ratings to know about offseason roster changes."""
    return min(st["gp"].get(h, 0), st["gp"].get(a, 0)) < config.EARLY_GAMES


def cap_early(pick):
    """Early season: picks top out at 'lean' (ratings are still mostly last season's)."""
    if pick and pick.get("tier") in ("play", "strong"):
        pick["tier"] = "lean"
        if "play" in pick:
            pick["play"] = False
        pick["early"] = True
    return pick


def ats_pick(proj, spread, team_name, h, a, home_odds=None, away_odds=None, move=None, dq=1.0):
    """Side, edge, tier, cover probability, EV and confidence vs the home spread."""
    margin = proj["margin"]
    side = "home" if margin + spread >= 0 else "away"
    sign = 1 if side == "home" else -1
    edge = abs(margin + spread)
    p_home = 1 - ratings.normal_cdf(-(margin + spread) / config.MARGIN_SD)
    p_cover = p_home if side == "home" else 1 - p_home
    odds = (home_odds if side == "home" else away_odds) or -110
    pick = {
        "ats_side": side, "ats_team": team_name(h if side == "home" else a), "ats_line": spread if side == "home" else -spread,
        "edge": round(edge, 1), "tier": tier(edge), "cover_prob": round(p_cover, 3),
        "ev": round(p_cover * (american_to_decimal(odds) - 1) - (1 - p_cover), 3),
    }
    covers = {k: sign * (proj["diffs"][k] + config.HOME_COURT * proj["hflag"] + proj["offset"] + spread) for k in SOURCES}
    detail = conf.score(
        side, covers, config.WEIGHTS, edge / config.STRONG_EDGE, 6.0,
        market=conf.market_component(sign, move, 2.0), trends=0.5, data=dq,
        high=config.CONF_HIGH, medium=config.CONF_MEDIUM,
        extra={"sources": {k: round(proj["diffs"][k] + config.HOME_COURT * proj["hflag"] + proj["offset"], 1) for k in SOURCES},
               "source_names": SOURCE_NAMES},
    )
    pick["confidence"], pick["conf_label"] = detail["score"], detail["label"]
    return pick, detail


def total_pick(model_total, line, over_odds=None, under_odds=None):
    return totals.make_pick(model_total, line, config.TOTAL_SD, config.TOTAL_LEAN_EDGE, config.TOTAL_PLAY_EDGE,
                            config.TOTAL_STRONG_EDGE, over_odds, under_odds)
