"""Diagnostic: The Odds API docs - player prop market keys and plan details."""
import re
import urllib.request


def text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (personal model; probe)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


for url, pats in [
    ("https://the-odds-api.com/sports-odds-data/betting-markets.html", ["anytime", "goal_scorer", "player_pass_tds", "Player Props"]),
    ("https://the-odds-api.com/liveapi/guides/v4/", ["events/{eventId}/odds", "usage quota", "does not count", "/events"]),
    ("https://the-odds-api.com/", ["500", "credits", "Starter", "player props", "Player props", "markets"]),
]:
    try:
        t = text(url)
    except Exception as e:
        print(url, "ERR", e)
        continue
    print("\n==", url, len(t))
    for p in pats:
        for m in list(re.finditer(re.escape(p), t))[:4]:
            print(f"  [{p}] ...{t[max(0, m.start() - 160):m.start() + 200]}...")
