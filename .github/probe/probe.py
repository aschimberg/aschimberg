"""Temporary: check NHL API odds + pbp fetch speed."""
import json, time, urllib.request
UA = {"User-Agent": "cfb-model-site/1.0 (+github actions; data probe)"}
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.loads(r.read())
base = "https://api-web.nhle.com/v1"
sc = get(f"{base}/score/2026-10-12")
print("oddsPartners:", json.dumps(sc.get("oddsPartners"))[:800])
fut = [g for g in sc.get("games", []) if g.get("gameState") in ("FUT", "PRE")]
print("future games:", len(fut))
if fut:
    g = fut[0]
    print("future game keys:", list(g.keys()))
    print("awayTeam:", json.dumps(g.get("awayTeam"))[:1200])
    print("homeTeam:", json.dumps(g.get("homeTeam"))[:1200])
for cc in ("US", "CA"):
    try:
        pg = get(f"{base}/partner-game/{cc}/now")
        print(f"partner-game {cc} keys:", list(pg.keys()))
        print(json.dumps(pg)[:2500])
    except Exception as e:
        print("partner-game", cc, "ERR", e)
# schedule for a date a week out - odds present?
try:
    s2 = get(f"{base}/schedule/2026-10-14")
    g = s2["gameWeek"][0]["games"][0]
    print("schedule game keys:", list(g.keys()))
    print("schedule away:", json.dumps(g.get("awayTeam"))[:600])
except Exception as e:
    print("sched ERR", e)
# timing of pbp fetches for last season
t = time.time(); n = 0
for gid in range(2025020001, 2025020021):
    try:
        p = get(f"{base}/gamecenter/{gid}/play-by-play"); n += 1
    except Exception as e:
        print("pbp ERR", gid, e)
print(f"fetched {n} pbp in {time.time()-t:.1f}s; last season sample date {p.get('gameDate')} plays {len(p.get('plays', []))}")
# season-level schedule for one club
try:
    cs = get(f"{base}/club-schedule-season/BOS/20262027")
    print("club schedule games:", len(cs.get("games", [])), "keys:", list(cs.keys()))
except Exception as e:
    print("club sched ERR", e)
