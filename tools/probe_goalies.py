"""Diagnostic: tonight's NHL goalies from ESPN probables, NHL rosters and ESPN injuries.

Usage (Actions probe workflow): module=probe_goalies. Optional env TEAMS=NJD,DAL,WPG.
"""
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "Mozilla/5.0 (personal sports model; probe)"}


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  {url}: {e}")
        return None


def main():
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    for d in (et.date(), et.date() + timedelta(days=1)):
        print(f"\n== ESPN probable starting goalies {d} ==")
        doc = get(f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={d:%Y%m%d}") or {}
        for ev in doc.get("events", []):
            comp = ev["competitions"][0]
            row = []
            for c in comp.get("competitors", []):
                g = [((p.get("athlete") or {}).get("fullName"), p.get("status", {}).get("name") if isinstance(p.get("status"), dict) else p.get("status"))
                     for p in c.get("probables") or [] if p.get("name") == "probableStartingGoalie"]
                row.append(f"{c['homeAway']} {c['team']['abbreviation']}: {g or 'none listed'}")
            print(f"  {ev.get('date')} {ev.get('shortName')} | " + " | ".join(row))

    teams = os.environ.get("TEAMS", "NJD,DAL,WPG").split(",")
    for t in teams:
        print(f"\n== NHL roster goalies {t} ==")
        doc = get(f"https://api-web.nhle.com/v1/roster/{t}/current") or {}
        for p in doc.get("goalies", []):
            print(f"  {p['firstName']['default']} {p['lastName']['default']} (id {p['id']})")

    print("\n== ESPN NHL injuries (goalies) ==")
    doc = get("https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries") or {}
    for team in doc.get("injuries", []):
        for i in team.get("injuries", []):
            a = i.get("athlete") or {}
            if (a.get("position") or {}).get("abbreviation") == "G":
                print(f"  {team.get('displayName')}: {a.get('displayName')} — {i.get('status')} — {(i.get('shortComment') or '')[:160]}")


if __name__ == "__main__":
    main()
