"""Blend SP+, FPI and Elo into a single points-scale power rating."""
from statistics import mean, pstdev

from . import config
from .cfbd import g
from .normalize import num


def _extract(sp_raw, fpi_raw, elo_raw):
    sp, off, de, conf = {}, {}, {}, {}
    for r in sp_raw or []:
        team = g(r, "team")
        rating = num(g(r, "rating"))
        if not team or team == "nationalAverages" or rating is None:
            continue
        sp[team] = rating
        conf[team] = g(r, "conference")
        o = num(g(g(r, "offense", {}), "rating"))
        d = num(g(g(r, "defense", {}), "rating"))
        if o is not None:
            off[team] = o
        if d is not None:
            de[team] = d
    fpi = {}
    for r in fpi_raw or []:
        team, v = g(r, "team"), num(g(r, "fpi"))
        if team and v is not None:
            fpi[team] = v
            conf.setdefault(team, g(r, "conference"))
    elo = {}
    for r in elo_raw or []:
        team, v = g(r, "team"), num(g(r, "elo"))
        if team and v is not None:
            elo[team] = v
            conf.setdefault(team, g(r, "conference"))
    return {"sp": sp, "fpi": fpi, "elo": elo}, off, de, conf


def _scale(values, teams, target_mean, target_sd):
    """Linearly map `values` so FBS teams have the target mean/std."""
    pool = [values[t] for t in teams if t in values] or list(values.values())
    if len(pool) < 2:
        return {}
    m, s = mean(pool), pstdev(pool) or 1.0
    return {t: target_mean + (v - m) / s * target_sd for t, v in values.items()}


def _blend_one_season(sp_raw, fpi_raw, elo_raw, fbs):
    src, off, de, conf = _extract(sp_raw, fpi_raw, elo_raw)
    teams = set(fbs) if fbs else set().union(*[set(v) for v in src.values()])

    # Anchor scale: SP+ if available, else FPI, else a generic points scale.
    anchor = next((k for k in ("sp", "fpi") if len([t for t in teams if t in src[k]]) >= 3), None)
    if anchor:
        pool = [src[anchor][t] for t in teams if t in src[anchor]]
        tm, ts = mean(pool), pstdev(pool) or 15.0
    else:
        tm, ts = 0.0, 15.0

    scaled = {k: _scale(v, teams, tm, ts) if v else {} for k, v in src.items()}
    out = {}
    for team in set().union(*[set(v) for v in scaled.values()]):
        parts = {k: scaled[k][team] for k in scaled if team in scaled[k]}
        w = sum(config.WEIGHTS[k] for k in parts)
        if w <= 0:
            continue
        rating = sum(config.WEIGHTS[k] * v for k, v in parts.items()) / w
        out[team] = {
            "team": team,
            "conference": conf.get(team),
            "rating": rating,
            "sp": src["sp"].get(team),
            "fpi": src["fpi"].get(team),
            "elo": src["elo"].get(team),
            "off": off.get(team),
            "def": de.get(team),
            "sources": sorted(parts),
            # Each source on the shared points scale; used for per-source
            # margins (confidence agreement + weekly weight tuning).
            "parts": {k: round(v, 2) for k, v in parts.items()},
            "fbs": team in teams if fbs else True,
        }
    lg_off = mean([off[t] for t in teams if t in off]) if off else None
    return out, lg_off


def blend(current, prior=None, fbs=None):
    """Build power ratings.

    current/prior: dicts with keys "sp", "fpi", "elo" holding raw CFBD lists.
    Returns (ratings_by_team, league_avg_offense).
    """
    cur, lg_off = _blend_one_season(current.get("sp"), current.get("fpi"), current.get("elo"), fbs)
    if prior:
        prev, prev_lg = _blend_one_season(prior.get("sp"), prior.get("fpi"), prior.get("elo"), fbs)
        lg_off = lg_off if lg_off is not None else prev_lg
        prev_fbs = [r["rating"] for r in prev.values() if r.get("fbs", True)]
        center = mean(prev_fbs) if prev_fbs else 0.0
        for team, r in prev.items():
            if team in cur or (fbs and team not in fbs):
                continue
            r = dict(r)
            r["rating"] = center + (r["rating"] - center) * config.PRIOR_SEASON_REGRESSION
            r["parts"] = {k: round(center + (v - center) * config.PRIOR_SEASON_REGRESSION, 2)
                          for k, v in r.get("parts", {}).items()}
            r["sources"] = ["prior"]
            cur[team] = r
    ranked = sorted((r for r in cur.values() if r.get("fbs", True)), key=lambda r: -r["rating"])
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    for r in cur.values():
        r["rating"] = round(r["rating"], 2)
    return cur, lg_off
