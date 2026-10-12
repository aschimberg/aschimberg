"""nflverse ingest: schedules/lines (games.csv) and play-by-play aggregates."""
import csv
import gzip
import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from cfbmodel import store
from sportscommon.http import fetch

from . import config, teams

ET = ZoneInfo("America/New_York")
POST_WEEK_LABEL = {"WC": "Wild Card", "DIV": "Divisional", "CON": "Conference Championships", "SB": "Super Bowl"}


def _num(v):
    try:
        return float(v) if v not in ("", "NA", None) else None
    except ValueError:
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def kickoff_utc(gameday, gametime):
    if not gameday:
        return None
    t = gametime or "13:00"
    dt = datetime.strptime(f"{gameday} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_games(text):
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        home, away = teams.norm(r["home_team"]), teams.norm(r["away_team"])
        result = _num(r.get("result"))
        spread_line = _num(r.get("spread_line"))  # nflverse: + = home favored
        out.append({
            "id": r["game_id"],
            "season": int(r["season"]),
            "game_type": r["game_type"],
            "season_type": "regular" if r["game_type"] == "REG" else "postseason",
            "week": int(r["week"]),
            "start": kickoff_utc(r.get("gameday"), r.get("gametime")),
            "gameday": r.get("gameday"),
            "home": home,
            "away": away,
            "home_pts": _num(r.get("home_score")),
            "away_pts": _num(r.get("away_score")),
            "completed": result is not None and r.get("home_score") not in ("", "NA"),
            "neutral": r.get("location") == "Neutral",
            "div_game": r.get("div_game") == "1",
            "spread": -spread_line if spread_line is not None else None,  # home perspective, - = home favored
            "total": _num(r.get("total_line")),
            "home_spread_odds": _num(r.get("home_spread_odds")),
            "away_spread_odds": _num(r.get("away_spread_odds")),
            "home_ml": _num(r.get("home_moneyline")),
            "away_ml": _num(r.get("away_moneyline")),
            "home_rest": _int(r.get("home_rest")),
            "away_rest": _int(r.get("away_rest")),
            "roof": r.get("roof") or None,
            "surface": r.get("surface") or None,
            "temp": _num(r.get("temp")),
            "wind": _num(r.get("wind")),
            "home_qb_id": r.get("home_qb_id") or None,
            "away_qb_id": r.get("away_qb_id") or None,
            "home_qb": r.get("home_qb_name") or None,
            "away_qb": r.get("away_qb_name") or None,
            "stadium": r.get("stadium") or None,
        })
    return out


def load_games(snapshot=False):
    """nflverse games.csv. snapshot=True also saves a gzipped copy for offline backtests."""
    text = fetch(config.GAMES_URL).decode("utf-8")
    if snapshot:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (config.CACHE_DIR / "games.csv.gz").write_bytes(gzip.compress(text.encode("utf-8")))
    return parse_games(text)


def load_games_snapshot():
    path = config.CACHE_DIR / "games.csv.gz"
    return parse_games(gzip.decompress(path.read_bytes()).decode("utf-8")) if path.exists() else None


def aggregate_pbp(rows):
    """Condense play-by-play rows into team-game offense lines and QB-game lines."""
    tg, qg = {}, {}
    lo, hi = config.GARBAGE_WP
    for r in rows:
        if r.get("season_type") not in ("REG", "POST"):
            continue
        if r.get("play_type") not in ("pass", "run"):
            continue
        if r.get("two_point_attempt") == "1" or r.get("qb_kneel") == "1" or r.get("qb_spike") == "1":
            continue
        epa = _num(r.get("epa"))
        wp = _num(r.get("wp"))
        if epa is None or wp is None or not (lo <= wp <= hi):
            continue
        off, dfn = teams.norm(r["posteam"]), teams.norm(r["defteam"])
        key = (r["game_id"], off)
        t = tg.get(key)
        if t is None:
            t = tg[key] = {
                "game_id": r["game_id"], "season": int(r["season"]), "week": int(r["week"]),
                "season_type": "regular" if r["season_type"] == "REG" else "postseason",
                "team": off, "opp": dfn, "home": 1 if off == teams.norm(r["home_team"]) else 0,
                "n": 0, "epa": 0.0, "succ": 0, "pass_n": 0, "pass_epa": 0.0, "rush_n": 0, "rush_epa": 0.0,
            }
        t["n"] += 1
        t["epa"] += epa
        t["succ"] += 1 if r.get("success") == "1" else 0
        if r.get("qb_dropback") == "1":
            t["pass_n"] += 1
            t["pass_epa"] += epa
            qb_id = r.get("id") or r.get("passer_player_id")
            if qb_id and qb_id != "NA":
                qk = (r["game_id"], qb_id)
                q = qg.get(qk)
                if q is None:
                    q = qg[qk] = {"game_id": r["game_id"], "season": int(r["season"]), "week": int(r["week"]),
                                  "team": off, "qb": qb_id, "name": r.get("name") or r.get("passer_player_name"),
                                  "db": 0, "epa": 0.0}
                q["db"] += 1
                q["epa"] += _num(r.get("qb_epa")) if _num(r.get("qb_epa")) is not None else epa
        else:
            t["rush_n"] += 1
            t["rush_epa"] += epa
    for t in tg.values():
        for k in ("epa", "pass_epa", "rush_epa"):
            t[k] = round(t[k], 3)
    for q in qg.values():
        q["epa"] = round(q["epa"], 3)
    return {"team_games": list(tg.values()), "qb_games": list(qg.values())}


def fetch_pbp(season):
    raw = fetch(config.PBP_URL.format(season=season))
    f = io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8")
    return aggregate_pbp(csv.DictReader(f))


def load_pbp(season, current, log=print):
    """Aggregated pbp for a season; completed seasons are cached in the repo."""
    path = config.CACHE_DIR / f"pbp_{season}.json"
    if season < current:
        cached = store.load(path, None)
        if cached is not None:
            return cached
    try:
        agg = fetch_pbp(season)
    except Exception as e:  # e.g. new season with no pbp file yet
        log(f"pbp {season} unavailable: {e}")
        return {"team_games": [], "qb_games": []}
    if season < current:
        store.save(path, agg, compact=True)
    log(f"pbp {season}: {len(agg['team_games'])} team-games, {len(agg['qb_games'])} QB-games")
    return agg
