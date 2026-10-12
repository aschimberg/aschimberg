"""Diagnostic: which CFBD ratings are available point-in-time (by week)?"""
import json

from cfbmodel.cfbd import CFBDClient


def short(x, n=600):
    return json.dumps(x)[:n]


c = CFBDClient()
for path, params in [
    ("/ratings/sp", {"year": 2025}),
    ("/ratings/sp", {"year": 2025, "week": 5}),
    ("/ratings/fpi", {"year": 2025}),
    ("/ratings/fpi", {"year": 2025, "week": 5}),
    ("/ratings/elo", {"year": 2025, "week": 5}),
    ("/metrics/wp/pregame", {"year": 2025, "week": 5}),
    ("/ratings/srs", {"year": 2025}),
    ("/ratings/sp", {"year": 2026}),
]:
    try:
        d = c.get(path, **params)
        rows = d if isinstance(d, list) else [d]
        ga = [r for r in rows if isinstance(r, dict) and (r.get("team") == "Georgia" or r.get("homeTeam") == "Georgia" or r.get("awayTeam") == "Georgia")]
        print(f"\n== {path} {params}: {len(rows)} rows; keys {list(rows[0].keys()) if rows and isinstance(rows[0], dict) else None}")
        print("Georgia:", short(ga[:2]))
    except Exception as e:
        print(f"\n== {path} {params}: ERROR {e}")
print("calls", c.calls)
