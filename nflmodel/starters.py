"""Starting-QB resolution for upcoming games.

nflverse's projected starters (games.csv) can lag by weeks (e.g. a backup
still listed after the starter returned), so before each game we check:

1. ESPN's current depth chart (QB1), skipping anyone ESPN's injury report
   lists as Out / Doubtful / Injured Reserve / Suspended;
2. if ESPN is unavailable, nflverse's projection, flagged when it differs from
   the QB who took most dropbacks in the team's last game (play-by-play);
3. with no projection, that last starter.

ESPN's public site API (site.api.espn.com) is read once per run.
"""
import json
import os
import re

from sportscommon.http import fetch

from . import teams

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
UNAVAILABLE = ("out", "doubtful", "injured reserve", "suspended", "physically unable", "non-football", "reserve")
_ESPN_ABBR = {"WSH": "WAS", "LAR": "LA"}


def norm_name(s):
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", (s or "").lower())
    return re.sub(r"[^a-z]", "", s)


def short_key(s):
    """'Sam Darnold' / 'S.Darnold' -> 'sdarnold' (first initial + last name)."""
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", (s or "").lower()).replace(".", ". ")
    parts = [p for p in re.split(r"[\s]+", s) if re.sub(r"[^a-z]", "", p)]
    if not parts:
        return ""
    return re.sub(r"[^a-z]", "", parts[0])[:1] + re.sub(r"[^a-z]", "", parts[-1])


def _get(url):
    return json.loads(fetch(url, retries=2, timeout=30).decode("utf-8"))


def fetch_espn(log=print, getter=None):
    """{'depth': {abbr: [QB names in order]}, 'skill': {abbr: [(pos, rank, name)]} for QB/RB/WR/TE/FB,
    'status': {(abbr, norm_name): injury status}} or None."""
    if getter is None and os.environ.get("NFL_ESPN", "on").lower() == "off":
        return None
    getter = getter or _get
    try:
        doc = getter(f"{ESPN}/teams")
        ids = {}
        for t in doc["sports"][0]["leagues"][0]["teams"]:
            t = t["team"]
            ids[teams.norm(_ESPN_ABBR.get(t["abbreviation"], t["abbreviation"]))] = (t["id"], t.get("displayName"))
        depth, skill = {}, {}
        for abbr, (tid, _) in ids.items():
            try:
                dc = getter(f"{ESPN}/teams/{tid}/depthcharts")
            except Exception as e:
                log(f"ESPN depth chart {abbr}: {e}")
                continue
            seen = set()
            for formation in dc.get("depthchart", []):
                positions = formation.get("positions") or {}
                qb = positions.get("qb")
                if qb and qb.get("athletes") and abbr not in depth:
                    depth[abbr] = [a.get("displayName") for a in qb["athletes"] if a.get("displayName")]
                for key, pos in positions.items():
                    base = re.sub(r"[^a-z]", "", key.lower())
                    base = "wr" if base in ("wr", "lwr", "rwr", "swr", "slwr") else base
                    if base not in ("qb", "rb", "wr", "te", "fb"):
                        continue
                    for rank, a in enumerate(pos.get("athletes") or [], 1):
                        nm = a.get("displayName")
                        if nm and nm not in seen:
                            seen.add(nm)
                            skill.setdefault(abbr, []).append((base, rank, nm))
        by_name = {disp: abbr for abbr, (_, disp) in ids.items()}
        status = {}
        inj = getter(f"{ESPN}/injuries")
        for t in inj.get("injuries", []):
            abbr = by_name.get(t.get("displayName"))
            if not abbr:
                continue
            for i in t.get("injuries", []):
                a = i.get("athlete") or {}
                if (a.get("position") or {}).get("abbreviation") in ("QB", "RB", "WR", "TE", "FB"):
                    status[(abbr, norm_name(a.get("displayName")))] = i.get("status") or ""
        log(f"ESPN: QB depth for {len(depth)} teams, skill players for {len(skill)}, {len(status)} injury entries")
        return {"depth": depth, "skill": skill, "status": status}
    except Exception as e:
        log(f"ESPN depth/injuries unavailable: {e}")
        return None


def unavailable(status):
    s = (status or "").lower()
    return any(s.startswith(u) for u in UNAVAILABLE)


def qb_directory(games, qb_rows):
    """Name lookups -> gsis id from every nflverse projected/actual starter and pbp passer."""
    full, short = {}, {}
    for g in games:
        for side in ("home", "away"):
            qid, nm = g.get(f"{side}_qb_id"), g.get(f"{side}_qb")
            if qid and nm:
                full[norm_name(nm)] = (qid, nm)
                short.setdefault((g[side], short_key(nm)), (qid, nm))
    for q in qb_rows:
        if q.get("name"):
            short.setdefault((q["team"], short_key(q["name"])), (q["qb"], q["name"]))
    return full, short


def last_starters(qb_rows):
    """team -> (qb_id, name) with most dropbacks in the team's latest game."""
    best = {}
    for q in qb_rows:
        k = q["team"]
        cur = best.get(k)
        key = (q["season"], q["week"], q["db"])
        if cur is None or key > cur[0]:
            best[k] = (key, q["qb"], q.get("name"))
    return {t: (qid, nm) for t, (_, qid, nm) in best.items()}


def resolve(team, projected_id, projected_name, espn, directory, last):
    """-> (qb_id or None, display name, source, note or None)."""
    full, short = directory

    def lookup(name):
        hit = full.get(norm_name(name)) or short.get((team, short_key(name)))
        return hit[0] if hit else None

    if espn and espn["depth"].get(team):
        for i, nm in enumerate(espn["depth"][team][:3]):
            st = espn["status"].get((team, norm_name(nm)), "")
            if unavailable(st):
                continue
            qid = lookup(nm)
            note = None
            if i > 0:
                above = espn["depth"][team][0]
                note = f"{above} {espn['status'].get((team, norm_name(above)), 'out').lower()}"
            elif st and st.lower() not in ("active",):
                note = st
            if projected_name and norm_name(projected_name) != norm_name(nm) and short_key(projected_name) != short_key(nm):
                note = (note + "; " if note else "") + f"nflverse listed {projected_name}"
            return qid, nm, "espn", note
    # No ESPN: a differing nflverse projection may be stale or may be a real
    # change (injury), and we can't tell which, so keep it but flag it.
    if projected_id:
        lq = last.get(team)
        note = f"unconfirmed; last start {lq[1]}" if lq and lq[0] != projected_id else None
        return projected_id, projected_name, "nflverse", note
    if team in last:
        qid, nm = last[team]
        return qid, nm, "last start", None
    return None, projected_name, "nflverse", None
