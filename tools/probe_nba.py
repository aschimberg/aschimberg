"""Diagnostic: what do the free NBA sources (ESPN public API, NBA CDN) return from GitHub Actions?"""
import json
import sys
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (personal sports model; probe)"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return None, f"ERR {e}"


def sec(t):
    print("\n" + "=" * 15, t, "=" * 15, flush=True)


def trunc(o, n=1500):
    return json.dumps(o)[:n]


def scoreboard(dates):
    st, body = get(f"{ESPN}/scoreboard?dates={dates}&limit=200")
    sec(f"scoreboard {dates} -> {st} {len(body)} bytes")
    if st != 200:
        return []
    d = json.loads(body)
    ev = d.get("events", [])
    print("top keys:", list(d.keys()), "events:", len(ev))
    for e in ev[:2]:
        c = e["competitions"][0]
        print("event keys:", list(e.keys()))
        print("id", e["id"], "date", e["date"], "season", e.get("season"), "status", trunc(c.get("status") or e.get("status"), 300))
        print("comp keys:", list(c.keys()), "neutral", c.get("neutralSite"))
        for t in c["competitors"]:
            print("  competitor:", t.get("homeAway"), t["team"].get("abbreviation"), t["team"].get("displayName"), "id", t["team"].get("id"),
                  "score", t.get("score"), "records", trunc(t.get("records"), 200))
        print("  odds:", trunc(c.get("odds"), 2500))
    return ev


if __name__ == "__main__":
    ev = scoreboard("20261020")
    scoreboard("20261021")
    old = scoreboard("20260115")
    scoreboard("20251021-20251031")
    sec("summary (boxscore) of a finished game")
    gid = (old or [{}])[0].get("id")
    if gid:
        st, body = get(f"{ESPN}/summary?event={gid}")
        print(st, len(body))
        if st == 200:
            d = json.loads(body)
            print("keys:", list(d.keys()))
            bx = d.get("boxscore", {})
            for t in bx.get("teams", []):
                print("team", t["team"].get("abbreviation"), "stats:", [(s.get("name"), s.get("displayValue"), s.get("label")) for s in t.get("statistics", [])])
            for t in bx.get("players", [])[:1]:
                st0 = t["statistics"][0]
                print("player stat keys:", list(st0.keys()), "labels:", st0.get("labels"), "names:", st0.get("names"))
                for a in st0.get("athletes", [])[:3]:
                    print("  ", trunc(a, 600))
            print("pickcenter:", trunc(d.get("pickcenter"), 1500))
            print("injuries:", trunc(d.get("injuries"), 800))
    sec("injuries")
    st, body = get(f"{ESPN}/injuries")
    print(st, len(body))
    if st == 200:
        d = json.loads(body)
        print("keys:", list(d.keys()))
        print(trunc(d.get("injuries", [])[:2], 2500))
    sec("teams")
    st, body = get(f"{ESPN}/teams")
    print(st, len(body))
    sec("NBA CDN schedule")
    st, body = get("https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json")
    print(st, len(body))
    sec("NBA CDN today's scoreboard")
    st, body = get("https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json")
    print(st, len(body), body[:600])
    sys.exit(0)
