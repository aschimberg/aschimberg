"""Diagnostic: where can current NFL starting QBs / injuries and NHL starting goalies come from?"""
import csv
import io
import json
import re
import sys
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (personal sports model; probe)"}


def get(url, limit=60_000_000):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            return r.status, r.read(limit).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return None, f"ERR {e}"


def sec(t):
    print("\n" + "=" * 15, t, "=" * 15, flush=True)


def csv_rows(url, pred, cols, n=25):
    st, body = get(url)
    sec(f"{url} -> {st} {len(body)} bytes")
    if st != 200:
        return
    rows = list(csv.DictReader(io.StringIO(body)))
    print("columns:", list(rows[0].keys()) if rows else None)
    hit = [r for r in rows if pred(r)]
    print("matching rows:", len(hit))
    for r in hit[-n:]:
        print({c: r.get(c) for c in cols if c in r})


def walk(o, path="", out=None, pat=re.compile(r"goalie|starter|probable|confirmed", re.I)):
    out = out if out is not None else []
    if isinstance(o, dict):
        for k, v in o.items():
            p = f"{path}.{k}"
            if pat.search(k):
                out.append((p, json.dumps(v)[:300]))
            walk(v, p, out, pat)
    elif isinstance(o, list):
        for i, v in enumerate(o[:5]):
            walk(v, f"{path}[{i}]", out, pat)
    return out


if __name__ == "__main__":
    base = "https://github.com/nflverse/nflverse-data/releases/download"
    csv_rows(f"{base}/injuries/injuries_2026.csv", lambda r: r.get("team") == "SEA" and r.get("position") == "QB",
             ["season", "week", "team", "full_name", "report_status", "practice_status", "report_primary_injury", "date_modified"])
    csv_rows(f"{base}/depth_charts/depth_charts_2026.csv", lambda r: (r.get("team") or r.get("club_code")) == "SEA" and (r.get("pos_abb") or r.get("position")) == "QB",
             ["dt", "season", "week", "team", "club_code", "player_name", "full_name", "pos_abb", "pos_rank", "depth_team", "gsis_id"])
    sec("ESPN NFL injuries")
    st, body = get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries")
    print(st, len(body))
    if st == 200:
        d = json.loads(body)
        for t in d.get("injuries", []):
            if "Seahawks" in t.get("displayName", "") or "Broncos" in t.get("displayName", ""):
                for i in t.get("injuries", []):
                    a = i.get("athlete", {})
                    print(t["displayName"], a.get("displayName"), (a.get("position") or {}).get("abbreviation"), i.get("status"), i.get("date"), (i.get("details") or {}).get("returnDate"))
    for u in ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/26/depthcharts",
              "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/2026/teams/26/depthcharts"):
        sec(u)
        st, body = get(u)
        print(st, len(body), body[:1500])
    nhl_id = sys.argv[1] if len(sys.argv) > 1 else "2026020100"
    for u in (f"https://api-web.nhle.com/v1/gamecenter/{nhl_id}/landing", f"https://api-web.nhle.com/v1/gamecenter/{nhl_id}/right-rail",
              "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"):
        sec(u)
        st, body = get(u)
        print(st, len(body))
        if st == 200:
            for p, v in walk(json.loads(body))[:25]:
                print(" ", p, "=", v)
    for u in ("https://www.dailyfaceoff.com/robots.txt", "https://www.dailyfaceoff.com/terms-of-service", "https://www.dailyfaceoff.com/terms"):
        sec(u)
        st, body = get(u)
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S)))
        print(st, len(body))
        if "robots" in u:
            print(body[:1500])
        for kw in ("scrap", "robot", "automat", "crawl", "data mining", "personal", "non-commercial"):
            m = re.search(kw, txt, re.I)
            if m:
                print(f"  [{kw}] ...{txt[max(0, m.start() - 250):m.start() + 250]}...")
