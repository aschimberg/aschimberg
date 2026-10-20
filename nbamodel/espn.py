"""ESPN's public NBA API (no key): scoreboard, box scores, odds, injuries.

    {ESPN}/scoreboard?dates=YYYYMMDD   games, scores, DraftKings spread/total/moneyline (open + current)
    {ESPN}/summary?event=ID            box score (team + player lines) and pickcenter (closing odds)
    {ESPN}/injuries                    league injury report

Parsers are tolerant: numbers arrive as '+102', '-4.5', 'o218.5', 'EVEN', etc.
"""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sportscommon import http

from . import config

ET = ZoneInfo("America/New_York")


def get(path, fetcher=None):
    url = f"{config.ESPN_API}/{path}"
    return (fetcher or http.fetch_json)(url)


def scoreboard(day, fetcher=None):
    return get(f"scoreboard?dates={day:%Y%m%d}&limit=200", fetcher)


def summary(gid, fetcher=None):
    return get(f"summary?event={gid}", fetcher)


def injuries(fetcher=None):
    return get("injuries", fetcher)


def num(x):
    """'+102' -> 102, 'o218.5' -> 218.5, 'EVEN' -> 100, 'PK' -> 0; None if not a number."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace("−", "-").replace("+", "")
    if s.upper() in ("EVEN", "EV"):
        return 100.0
    if s.upper() in ("PK", "PICK"):
        return 0.0
    s = s.lstrip("oOuU")
    try:
        return float(s)
    except ValueError:
        return None


def _dig(o, *keys):
    for k in keys:
        if not isinstance(o, dict):
            return None
        o = o.get(k)
    return o


def iso_z(s):
    """'2026-10-20T19:00Z' -> '2026-10-20T19:00:00Z' (sortable against our timestamps)."""
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def et_date(start):
    return datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(ET).date().isoformat()


def parse_odds(o, home_abbr=None):
    """One ESPN odds/pickcenter entry -> {spread (home), total, moneylines, prices, opens, book} or None."""
    if not o:
        return None
    prov = o.get("provider") or {}
    out = {"book": prov.get("displayName") or prov.get("name") or "ESPN"}
    ps = o.get("pointSpread") or {}
    spread = num(_dig(ps, "home", "close", "line"))
    if spread is None:
        spread = num(o.get("spread"))
    if spread is None and o.get("details") and home_abbr:
        m = re.match(r"^\s*(\S+)\s+([-+]?\d+(?:\.\d+)?)", o["details"])
        if m:
            v = float(m.group(2))
            spread = v if m.group(1) == home_abbr else -v
        elif o["details"].strip().upper() in ("EVEN", "PK", "PICK"):
            spread = 0.0
    out["spread"] = spread
    out["spread_open"] = num(_dig(ps, "home", "open", "line"))
    hto, ato = o.get("homeTeamOdds") or {}, o.get("awayTeamOdds") or {}
    out["home_spread_odds"] = num(_dig(ps, "home", "close", "odds")) or num(hto.get("spreadOdds"))
    out["away_spread_odds"] = num(_dig(ps, "away", "close", "odds")) or num(ato.get("spreadOdds"))
    tot = o.get("total") or {}
    out["total"] = num(o.get("overUnder"))
    if out["total"] is None:
        out["total"] = num(_dig(tot, "over", "close", "line"))
    out["total_open"] = num(_dig(tot, "over", "open", "line"))
    out["over_odds"] = num(_dig(tot, "over", "close", "odds")) or num(o.get("overOdds"))
    out["under_odds"] = num(_dig(tot, "under", "close", "odds")) or num(o.get("underOdds"))
    ml = o.get("moneyline") or {}
    out["ml_home"] = num(_dig(ml, "home", "close", "odds")) or num(hto.get("moneyLine"))
    out["ml_away"] = num(_dig(ml, "away", "close", "odds")) or num(ato.get("moneyLine"))
    out["ml_home_open"] = num(_dig(ml, "home", "open", "odds"))
    out["ml_away_open"] = num(_dig(ml, "away", "open", "odds"))
    if out["spread"] is None and out["total"] is None and out["ml_home"] is None:
        return None
    return {k: v for k, v in out.items() if v is not None}


def parse_event(e):
    """Scoreboard event -> (game dict, {abbr: team info}) or (None, {}) for non-NBA/preseason/all-star."""
    c = (e.get("competitions") or [{}])[0]
    comps = {t.get("homeAway"): t for t in c.get("competitors", [])}
    if "home" not in comps or "away" not in comps:
        return None, {}
    abbr = lambda t: (t.get("team") or {}).get("abbreviation")
    h, a = abbr(comps["home"]), abbr(comps["away"])
    season = e.get("season") or {}
    stype = season.get("type")
    if h not in config.TEAMS or a not in config.TEAMS or stype not in (2, 3, 5):
        return None, {}
    st = (c.get("status") or e.get("status") or {}).get("type") or {}
    state = st.get("state") or "pre"
    start = iso_z(e.get("date") or c.get("date"))

    def score(t):
        try:
            return int(float(t.get("score"))) if state != "pre" and t.get("score") not in (None, "") else None
        except (TypeError, ValueError):
            return None

    g = {
        "id": str(e["id"]), "start": start, "date": et_date(start) if start else None, "season": season.get("year"),
        "season_type": "regular" if stype == 2 else "postseason", "playin": stype == 5,
        "neutral": bool(c.get("neutralSite")), "home": h, "away": a,
        "home_score": score(comps["home"]), "away_score": score(comps["away"]),
        "state": state, "completed": bool(st.get("completed")),
        "detail": st.get("shortDetail"),
        "venue": (c.get("venue") or {}).get("fullName"),
        "tv": [n for b in c.get("broadcasts", []) for n in (b.get("names") or [])[:1] if n] or None,
        "odds": parse_odds((c.get("odds") or [None])[0], h),
    }
    teams = {}
    for side in ("home", "away"):
        t = comps[side].get("team") or {}
        rec = next((r.get("summary") for r in comps[side].get("records") or [] if r.get("type") == "total"), None)
        teams[t.get("abbreviation")] = {"id": t.get("id"), "name": t.get("displayName"), "short": t.get("shortDisplayName") or t.get("name"),
                                        "logo": t.get("logo"), "record": rec}
    return g, teams


def calendar_dates(doc):
    """Season dates from a scoreboard's league calendar (ESPN lists every game day)."""
    out = []
    for lg in doc.get("leagues") or []:
        for item in lg.get("calendar") or []:
            s = item if isinstance(item, str) else (item.get("startDate") if isinstance(item, dict) else None)
            if s:
                try:
                    out.append(datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(ET).date())
                except ValueError:
                    pass
    return sorted(set(out))


def _made_att(v):
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)", str(v or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def game_score(s):
    """Hollinger Game Score from a box-score line {stat: value}."""
    fgm, fga = _made_att(s.get("FG"))
    ftm, fta = _made_att(s.get("FT"))
    g = lambda k: num(s.get(k)) or 0.0
    return (g("PTS") + 0.4 * fgm - 0.7 * fga - 0.4 * (fta - ftm) + 0.7 * g("OREB") + 0.3 * g("DREB")
            + g("STL") + 0.7 * g("AST") + 0.7 * g("BLK") - 0.4 * g("PF") - g("TO"))


def parse_summary(d, home=None, away=None):
    """Summary -> {'close': odds, 'team': {abbr: {'poss'}}, 'players': [[pid, name, abbr, min, gmsc, starter]]}."""
    out = {"close": None, "team": {}, "players": []}
    pc = d.get("pickcenter") or d.get("odds") or []
    if pc:
        pick = next((p for p in pc if "draft" in str(_dig(p, "provider", "name") or "").lower()), pc[0])
        out["close"] = parse_odds(pick, home)
    bx = d.get("boxscore") or {}
    for t in bx.get("teams") or []:
        ab = (t.get("team") or {}).get("abbreviation")
        st = {s.get("name"): s.get("displayValue") for s in t.get("statistics") or []}
        fgm, fga = _made_att(st.get("fieldGoalsMade-fieldGoalsAttempted"))
        ftm, fta = _made_att(st.get("freeThrowsMade-freeThrowsAttempted"))
        oreb = num(st.get("offensiveRebounds")) or 0.0
        tov = num(st.get("totalTurnovers")) if st.get("totalTurnovers") is not None else num(st.get("turnovers"))
        if fga:
            out["team"][ab] = {"poss": round(fga - oreb + (tov or 0.0) + 0.44 * fta, 1), "fga": fga, "fta": fta}
    for t in bx.get("players") or []:
        ab = (t.get("team") or {}).get("abbreviation")
        for blk in (t.get("statistics") or [])[:1]:
            names = blk.get("names") or blk.get("labels") or []
            for a in blk.get("athletes") or []:
                vals = a.get("stats") or []
                if a.get("didNotPlay") or len(vals) != len(names):
                    continue
                s = dict(zip(names, vals))
                mins = num(s.get("MIN"))
                if not mins:
                    continue
                ath = a.get("athlete") or {}
                out["players"].append([str(ath.get("id")), ath.get("displayName"), ab, round(mins, 1),
                                       round(game_score(s), 1), 1 if a.get("starter") else 0])
    return out


_ID_RE = re.compile(r"/id/(\d+)")


def parse_injuries(d, name_to_abbr):
    """{abbr: [{'id', 'name', 'status', 'date', 'comment'}]} from the league injury report."""
    out = {}
    for tb in d.get("injuries") or []:
        ab = name_to_abbr.get(tb.get("displayName")) or tb.get("abbreviation")
        if ab not in config.TEAMS:
            continue
        for inj in tb.get("injuries") or []:
            ath = inj.get("athlete") or {}
            pid = ath.get("id")
            if not pid:
                for ln in ath.get("links") or []:
                    m = _ID_RE.search(ln.get("href") or "")
                    if m:
                        pid = m.group(1)
                        break
            status = inj.get("status") or (inj.get("type") or {}).get("description")
            out.setdefault(ab, []).append({"id": str(pid) if pid else None, "name": ath.get("displayName"),
                                           "status": status, "date": inj.get("date"),
                                           "comment": inj.get("shortComment")})
    return out
