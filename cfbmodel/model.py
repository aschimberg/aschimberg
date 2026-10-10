"""Score/spread projections and ATS/SU picks."""
import math

from . import config


def win_prob(margin, sd=None):
    sd = sd or config.MARGIN_SD
    return 0.5 * (1 + math.erf(margin / (sd * math.sqrt(2))))


def team_rating(ratings, team):
    r = ratings.get(team)
    return r["rating"] if r else config.NON_FBS_RATING


def project(game, ratings, lg_off=None, market_total=None):
    """Project a game. Margin is from the home team's perspective."""
    hfa = 0.0 if game.get("neutral") else config.HOME_FIELD
    margin = team_rating(ratings, game["home"]) - team_rating(ratings, game["away"]) + hfa

    total = None
    h, a = ratings.get(game["home"]), ratings.get(game["away"])
    if lg_off is not None and h and a and None not in (h.get("off"), h.get("def"), a.get("off"), a.get("def")):
        # SP+ offense = points scored vs avg defense; defense = points allowed
        # vs avg offense. Expected points ~ own offense + opp defense - avg.
        home_pts = h["off"] + a["def"] - lg_off
        away_pts = a["off"] + h["def"] - lg_off
        total = home_pts + away_pts
    if total is None:
        total = market_total if market_total else config.DEFAULT_TOTAL
    total = max(total, abs(margin) + 7)
    home_pts = (total + margin) / 2
    away_pts = (total - margin) / 2
    return {
        "margin": round(margin, 1),
        "spread": round(-margin, 1),  # model line, home perspective
        "total": round(total, 1),
        "home_pts": round(home_pts, 1),
        "away_pts": round(away_pts, 1),
        "home_wp": round(win_prob(margin), 3),
    }


def tier(edge):
    if edge >= config.STRONG_EDGE:
        return "strong"
    if edge >= config.PLAY_EDGE:
        return "play"
    if edge >= config.LEAN_EDGE:
        return "lean"
    return "pass"


def fmt_spread(s):
    if s is None:
        return "—"
    if s == 0:
        return "PK"
    return f"{s:+g}"


def make_pick(game, proj, line):
    """ATS pick vs `line` (home spread) plus straight-up pick."""
    su_team = game["home"] if proj["margin"] >= 0 else game["away"]
    su_wp = proj["home_wp"] if proj["margin"] >= 0 else 1 - proj["home_wp"]
    pick = {
        "su_team": su_team,
        "su_wp": round(su_wp, 3),
        "ats_side": None,
        "ats_team": None,
        "ats_line": None,
        "edge": None,
        "tier": None,
    }
    if line is None or line.get("spread") is None:
        return pick
    s = line["spread"]
    cover = proj["margin"] + s  # >0 -> home covers in expectation
    side = "home" if cover > 0 else "away"
    pick.update(
        {
            "ats_side": side,
            "ats_team": game[side],
            "ats_line": s if side == "home" else -s,
            "edge": round(abs(cover), 1),
            "tier": tier(abs(cover)),
        }
    )
    return pick


def grade(pick_side, line_home, home_pts, away_pts):
    """Grade an ATS pick. Returns 'W', 'L' or 'P'."""
    adj = (home_pts - away_pts) + line_home
    if abs(adj) < 1e-9:
        return "P"
    home_covered = adj > 0
    return "W" if home_covered == (pick_side == "home") else "L"
