"""MoneyPuck.com season summaries (team expected goals, goalie GSAx).

Data: https://moneypuck.com/data.htm, free for non-commercial use with credit
to MoneyPuck.com. Only the files listed there are used: per-season
teams.csv and goalies.csv. MoneyPuck's xG model adds features ours doesn't
(shooter talent, flurry and score/venue adjustment), so it serves as a
fourth team-strength source and a second goalie GSAx estimate.
"""
import csv
import io

from sportscommon.http import fetch

from . import config

URL = "https://moneypuck.com/moneypuck/playerData/seasonSummary/{year}/regular/{kind}.csv"
TEAM_CODES = {"N.J": "NJD", "S.J": "SJS", "T.B": "TBL", "L.A": "LAK"}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _rows(text):
    r = csv.reader(io.StringIO(text))
    header = next(r, [])
    # teams.csv repeats the "team" column; keep the first occurrence of each name
    idx = {}
    for i, h in enumerate(header):
        idx.setdefault(h, i)
    for row in r:
        yield {h: row[i] for h, i in idx.items() if i < len(row)}


def parse_teams(text):
    out = {}
    for r in _rows(text):
        if r.get("situation") != "all":
            continue
        t = TEAM_CODES.get(r.get("team"), r.get("team"))
        gp = _f(r.get("games_played")) or 0
        xgf = _f(r.get("flurryScoreVenueAdjustedxGoalsFor")) or _f(r.get("scoreVenueAdjustedxGoalsFor")) or _f(r.get("xGoalsFor"))
        xga = _f(r.get("flurryScoreVenueAdjustedxGoalsAgainst")) or _f(r.get("scoreVenueAdjustedxGoalsAgainst")) or _f(r.get("xGoalsAgainst"))
        if gp and xgf is not None and xga is not None:
            out[t] = {"gp": gp, "xgf": xgf, "xga": xga, "xg_pct": _f(r.get("xGoalsPercentage"))}
    return out


def parse_goalies(text):
    out = {}
    for r in _rows(text):
        if r.get("situation") != "all":
            continue
        try:
            pid = int(r.get("playerId"))
        except (TypeError, ValueError):
            continue
        xg, g, shots = _f(r.get("xGoals")), _f(r.get("goals")), _f(r.get("unblocked_shot_attempts"))
        if xg is None or g is None or not shots:
            continue
        out[pid] = {"xg": xg, "goals": g, "shots": shots, "name": r.get("name"),
                    "team": TEAM_CODES.get(r.get("team"), r.get("team")), "gp": _f(r.get("games_played"))}
    return out


def load(season_id, log=print, fetcher=None):
    """{'cur': {'teams','goalies'}, 'prev': {...}} for an NHL season id like 20262027, or None."""
    fetcher = fetcher or (lambda u: fetch(u, retries=2, timeout=60).decode("utf-8"))
    year = season_id // 10000
    out = {}
    cache = config.DATA_DIR / "cache"
    for key, y in (("cur", year), ("prev", year - 1)):
        part = {}
        for kind, parser in (("teams", parse_teams), ("goalies", parse_goalies)):
            path = cache / f"moneypuck_{y}_{kind}.csv"
            text = None
            if key == "prev" and path.exists():
                text = path.read_text()
            else:
                try:
                    text = fetcher(URL.format(year=y, kind=kind))
                    cache.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
                except Exception as e:
                    log(f"MoneyPuck {y} {kind}: {e}")
                    text = path.read_text() if path.exists() else None
            part[kind] = parser(text) if text else {}
        out[key] = part
    if not out["cur"]["teams"] and not out["prev"]["teams"]:
        return None
    log(f"MoneyPuck: {len(out['cur']['teams'])} teams, {len(out['cur']['goalies'])} goalies this season")
    return out


def team_strength(mp, prior_games=None):
    """{team: (off, def)} in xG per game vs league average; this season shrunk
    toward last season's per-game rates (prior_games of weight)."""
    k = prior_games if prior_games is not None else config.RIDGE_GAMES
    cur, prev = mp["cur"]["teams"], mp["prev"]["teams"]

    def pg(d, t, f):
        r = d.get(t)
        return r[f] / r["gp"] if r and r["gp"] else None

    teams = set(cur) | set(prev)
    prev_mean = (sum(r["xgf"] for r in prev.values()) / sum(r["gp"] for r in prev.values())) if prev else None
    rates = {}
    for t in teams:
        c = cur.get(t, {"gp": 0, "xgf": 0.0, "xga": 0.0})
        pf = pg(prev, t, "xgf") or prev_mean or 0.0
        pa = pg(prev, t, "xga") or prev_mean or 0.0
        n = c["gp"] + k
        rates[t] = ((c["xgf"] + k * pf) / n, (c["xga"] + k * pa) / n)
    if not rates:
        return {}
    mean = sum(f + a for f, a in rates.values()) / (2 * len(rates))
    return {t: (f - mean, mean - a) for t, (f, a) in rates.items()}


def goalie_rates(mp, n0=None, prev_weight=None):
    """{playerId: {'rate': shrunk GSAx per unblocked shot, 'shots'}}."""
    n0 = n0 if n0 is not None else config.GOALIE_PRIOR_SHOTS
    pw = prev_weight if prev_weight is not None else config.GOALIE_LAST_SEASON_WEIGHT
    acc = {}
    for w, d in ((1.0, mp["cur"]["goalies"]), (pw, mp["prev"]["goalies"])):
        for pid, r in d.items():
            a = acc.setdefault(pid, [0.0, 0.0])
            a[0] += w * (r["xg"] - r["goals"])
            a[1] += w * r["shots"]
    return {pid: {"rate": v / (n + n0), "shots": n} for pid, (v, n) in acc.items()}
