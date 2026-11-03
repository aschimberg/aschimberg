"""Diagnostic: D1 men's college basketball sources (ESPN, Bart Torvik) reachable from Actions.

Usage (Actions probe workflow): module=probe_cbb.
"""
import gzip
import json
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (personal sports model; probe)"}
SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"


def get(url, raw=False):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            body = r.read()
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            print(f"  OK {r.status} {len(body)}B {url}")
            return body if raw else json.loads(body.decode("utf-8"))
    except Exception as e:
        print(f"  FAIL {url}: {str(e)[:200]}")
        return None


def scoreboard(d):
    doc = get(f"{SITE}/scoreboard?dates={d}&groups=50&limit=500") or {}
    evs = doc.get("events", [])
    with_odds = [e for e in evs if (e["competitions"][0].get("odds") or [])]
    print(f"  {d}: {len(evs)} D1 events, {len(with_odds)} with odds")
    if with_odds:
        o = with_odds[0]["competitions"][0]["odds"][0]
        print("   sample odds:", {k: o.get(k) for k in ("provider", "details", "overUnder", "spread")})
    return evs


def summary(eid):
    doc = get(f"{SITE}/summary?event={eid}") or {}
    pc = doc.get("pickcenter") or []
    print(f"  summary {eid}: keys={sorted(doc.keys())}")
    print(f"   pickcenter={[(p.get('provider', {}).get('name'), p.get('spread'), p.get('overUnder')) for p in pc][:3]}")
    teams = (doc.get("boxscore") or {}).get("teams") or []
    if teams:
        print("   team stat names:", [s.get("name") for s in teams[0].get("statistics", [])][:25])
    print(f"   plays={len(doc.get('plays') or [])}")


CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball"


def past_odds(eid):
    doc = get(f"{SITE}/summary?event={eid}") or {}
    print("   summary odds:", str(doc.get("odds"))[:400])
    print("   againstTheSpread:", str(doc.get("againstTheSpread"))[:300])
    core = get(f"{CORE}/events/{eid}/competitions/{eid}/odds") or {}
    for it in (core.get("items") or [])[:3]:
        print("   core odds:", {k: it.get(k) for k in ("provider", "details", "overUnder", "spread")},
              "open/close:", str({k: it.get(k) for k in ("open", "close", "current")})[:300])


def main():
    print("== past-game odds ==")
    for d in ("20251104", "20251201"):
        evs = scoreboard(d)
        fin = [e for e in evs if e["status"]["type"].get("completed")]
        for e in fin[:2]:
            past_odds(e["id"])
    return
    print("== ESPN D1 scoreboard: last season early games and today ==")
    old = scoreboard("20251110")
    scoreboard("20261103")
    scoreboard("20261104")
    if old:
        fin = [e for e in old if e["status"]["type"].get("completed")]
        for e in fin[:2]:
            summary(e["id"])
    print("\n== ESPN teams (D1 count) ==")
    doc = get(f"{SITE}/teams?groups=50&limit=500") or {}
    teams = ((doc.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []
    print(f"  {len(teams)} teams")

    print("\n== Bart Torvik ==")
    for url in ("https://barttorvik.com/2026_team_results.json",
                "https://barttorvik.com/2027_team_results.json",
                "https://barttorvik.com/timemachine/team_results/20251110_team_results.json.gz",
                "https://barttorvik.com/2026_fffinal.csv",
                "https://barttorvik.com/trank.php?year=2027&json=1"):
        body = get(url, raw=True)
        if body:
            print("   head:", body[:300].decode("utf-8", "replace").replace("\n", " "))


if __name__ == "__main__":
    main()
