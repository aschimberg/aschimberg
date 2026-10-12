"""Probable starting goalies from ESPN's public NHL scoreboard.

ESPN lists a "Probable Starting Goalie" per team once it's reported (usually
the morning of the game). Names are matched to NHL player ids through the
goalies we've seen in play-by-play.
"""
import json
import os
import re

from sportscommon.http import fetch

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={ymd}"


def short_key(s):
    """'Logan Thompson' / 'L. Thompson' -> 'lthompson'."""
    s = (s or "").lower().replace(".", ". ")
    parts = [re.sub(r"[^a-z]", "", p) for p in s.split()]
    parts = [p for p in parts if p]
    return (parts[0][:1] + parts[-1]) if parts else ""


def parse(doc, team_by_name):
    """{(date_iso, team_abbr): goalie full name} from one scoreboard response."""
    out = {}
    for ev in (doc or {}).get("events", []):
        for comp in ev.get("competitions", []):
            d = (comp.get("date") or ev.get("date") or "")[:10]
            for c in comp.get("competitors", []):
                abbr = team_by_name.get((c.get("team") or {}).get("displayName"))
                for p in c.get("probables") or []:
                    if p.get("name") == "probableStartingGoalie" and abbr:
                        nm = (p.get("athlete") or {}).get("fullName") or (p.get("athlete") or {}).get("displayName")
                        if nm:
                            out[(abbr, nm)] = d
    return out


def fetch_probables(dates, team_by_name, log=print, getter=None):
    """{team_abbr: [(utc_date, name)]} for the given ET dates (date objects)."""
    if getter is None and os.environ.get("NHL_ESPN", "on").lower() == "off":
        return {}
    getter = getter or (lambda u: json.loads(fetch(u, retries=2, timeout=30).decode("utf-8")))
    out = {}
    for d in dates:
        try:
            doc = getter(SCOREBOARD.format(ymd=d.strftime("%Y%m%d")))
        except Exception as e:
            log(f"ESPN NHL scoreboard {d}: {e}")
            continue
        for (abbr, nm), _ in parse(doc, team_by_name).items():
            out.setdefault((d.isoformat(), abbr), nm)
    if out:
        log(f"ESPN: {len(out)} probable starting goalies")
    return out


def match(name, team, starts, gnames):
    """NHL goalie id for an ESPN name: team's recent goalies first, then anyone."""
    k = short_key(name)
    for _, gid in reversed(starts.get(team, [])):
        if short_key(gnames.get(gid)) == k:
            return gid
    for gid, nm in gnames.items():
        if short_key(nm) == k:
            return gid
    return None
