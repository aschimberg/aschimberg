"""Temporary: print sample structure of NFL/NHL data sources."""
import csv, gzip, io, json, sys, urllib.request
from datetime import date, timedelta

UA = {"User-Agent": "cfb-model-site/1.0 (+github actions; data probe)"}

def get(url, n=None):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read() if n is None else r.read(n)

def show(title, obj, limit=3500):
    print(f"\n===== {title}")
    s = obj if isinstance(obj, str) else json.dumps(obj, indent=1)[:limit]
    print(s[:limit])

# --- NFL schedules / lines
for url in ["https://github.com/nflverse/nfldata/raw/master/data/games.csv",
            "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"]:
    try:
        raw = get(url).decode()
        rows = list(csv.DictReader(io.StringIO(raw)))
        show(f"games.csv {url} rows={len(rows)}", ", ".join(rows[0].keys()))
        cur = [r for r in rows if r["season"] == "2026"]
        weeks = sorted({int(r["week"]) for r in cur})
        show("2026 weeks", str(weeks))
        done = [r for r in cur if r["result"]]
        todo = [r for r in cur if not r["result"]]
        show("completed sample", done[-1] if done else {})
        show("upcoming sample (first 2)", todo[:2])
        show("upcoming lines filled", str(sum(1 for r in todo if r["spread_line"])) + " of " + str(len(todo)) +
             " | qb names filled: " + str(sum(1 for r in todo if r.get("home_qb_name"))))
        break
    except Exception as e:
        print("ERR", url, e)

# --- NFL pbp
for url in ["https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_2026.csv.gz",
            "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_2025.csv.gz"]:
    try:
        raw = get(url)
        print(f"\n===== pbp {url} bytes={len(raw)}")
        f = io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8")
        rd = csv.DictReader(f)
        cols = rd.fieldnames
        print("ncols", len(cols))
        want = ["game_id","season","week","season_type","posteam","defteam","home_team","away_team","play_type","pass","rush",
                "qb_dropback","epa","success","wp","qtr","down","ydstogo","yardline_100","passer_player_id","passer_player_name",
                "id","name","cpoe","qb_epa","score_differential","half_seconds_remaining","game_seconds_remaining","penalty",
                "special_teams_play","two_point_attempt","qb_kneel","qb_spike","fumble_lost","interception","play_id","vegas_wp"]
        print("present:", [c for c in want if c in cols])
        print("missing:", [c for c in want if c not in cols])
        n = 0
        for r in rd:
            n += 1
            if n == 200:
                print({k: r.get(k) for k in want if k in cols})
        print("rows", n)
        break
    except Exception as e:
        print("ERR", url, e)

# --- NHL
base = "https://api-web.nhle.com/v1"
today = date(2026, 10, 12)
try:
    sch = json.loads(get(f"{base}/schedule/{today}"))
    show("nhl schedule keys", list(sch.keys()))
    gw = sch.get("gameWeek", [])
    show("nhl gameWeek[0] keys/date", {"date": gw[0].get("date"), "keys": list(gw[0].keys()), "ngames": len(gw[0].get("games", []))} if gw else {})
    g0 = next((g for d in gw for g in d.get("games", [])), None)
    show("nhl schedule game sample", g0 or {}, 4000)
except Exception as e:
    print("ERR schedule", e)
try:
    sc = json.loads(get(f"{base}/score/{today - timedelta(days=2)}"))
    games = sc.get("games", [])
    show("nhl score keys", list(sc.keys()))
    fin = next((g for g in games if g.get("gameState") in ("OFF", "FINAL")), games[0] if games else None)
    show("nhl score game", fin or {}, 3000)
    gid = fin["id"] if fin else None
except Exception as e:
    print("ERR score", e); gid = None
if gid:
    try:
        pbp = json.loads(get(f"{base}/gamecenter/{gid}/play-by-play"))
        show("pbp keys", list(pbp.keys()))
        plays = pbp.get("plays", [])
        types = {}
        for p in plays:
            types[p.get("typeDescKey")] = types.get(p.get("typeDescKey"), 0) + 1
        show("pbp event types", types)
        shot = next((p for p in plays if p.get("typeDescKey") in ("shot-on-goal", "goal")), None)
        show("pbp shot sample", shot or {})
        show("pbp rosterSpots[0]", (pbp.get("rosterSpots") or [{}])[0])
        show("pbp homeTeam", pbp.get("homeTeam", {}))
    except Exception as e:
        print("ERR pbp", e)
    try:
        box = json.loads(get(f"{base}/gamecenter/{gid}/boxscore"))
        show("box keys", list(box.keys()))
        pg = box.get("playerByGameStats", {})
        show("box goalies", pg.get("homeTeam", {}).get("goalies", []))
    except Exception as e:
        print("ERR box", e)
    try:
        land = json.loads(get(f"{base}/gamecenter/{gid}/landing"))
        show("landing keys", list(land.keys()))
    except Exception as e:
        print("ERR landing", e)
# an upcoming game's landing (probable goalies?)
try:
    up = next((g for d in gw for g in d.get("games", []) if g.get("gameState") in ("FUT", "PRE")), None)
    if up:
        land = json.loads(get(f"{base}/gamecenter/{up['id']}/landing"))
        show("upcoming landing keys", list(land.keys()))
        show("upcoming landing matchup", land.get("matchup", {}), 3000)
except Exception as e:
    print("ERR upcoming landing", e)
try:
    st = json.loads(get(f"{base}/standings/now"))
    show("standings sample", (st.get("standings") or [{}])[0], 2500)
except Exception as e:
    print("ERR standings", e)
