"""Diagnostic: which free news feeds answer from GitHub Actions, and what do they return?"""
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = {"User-Agent": "Mozilla/5.0 (personal sports model; news probe)"}


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            return r.status, r.read().decode("utf-8", "replace"), r.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        return e.code, "", None
    except Exception as e:
        return None, f"ERR {e}", None


def rss(name, url):
    st, body, ct = get(url)
    print(f"\n=== {name} -> {st} {ct} {len(body)} bytes\n    {url}", flush=True)
    if st != 200:
        print("   ", body[:200])
        return
    try:
        root = ET.fromstring(body.encode())
    except ET.ParseError as e:
        print("    not XML:", e, body[:200])
        return
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    print("    items:", len(items))
    for it in items[:3]:
        f = lambda t: (it.findtext(t) or it.findtext("{http://www.w3.org/2005/Atom}" + t) or "").strip()
        print("   -", f("title")[:110], "|", f("pubDate") or f("updated"), "|", (f("description") or "")[:120].replace("\n", " "))


def espn(name, url):
    st, body, ct = get(url)
    print(f"\n=== {name} -> {st} {len(body)} bytes\n    {url}", flush=True)
    if st != 200:
        return
    d = json.loads(body)
    arts = d.get("articles") or []
    print("    articles:", len(arts), "keys:", list(arts[0].keys()) if arts else None)
    for a in arts[:3]:
        cats = [(c.get("type"), c.get("description") or (c.get("athlete") or {}).get("description")) for c in a.get("categories") or []][:5]
        print("   -", a.get("headline", "")[:100], "|", a.get("published"), "|", cats)


g = lambda q: "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})

espn("ESPN NFL news", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?limit=50")
espn("ESPN NFL team news (BAL=33)", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?team=33&limit=20")
espn("ESPN NBA news", "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news?limit=20")
espn("ESPN NHL news", "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/news?limit=20")
espn("ESPN CFB news", "https://site.api.espn.com/apis/site/v2/sports/football/college-football/news?limit=20")
rss("Google News: Baltimore Ravens 2d", g('"Baltimore Ravens" when:2d'))
rss("Google News: Lamar Jackson 3d", g('"Lamar Jackson" when:3d'))
rss("CBS Sports NFL", "https://www.cbssports.com/rss/headlines/nfl/")
rss("CBS Sports NBA", "https://www.cbssports.com/rss/headlines/nba/")
rss("CBS Sports NHL", "https://www.cbssports.com/rss/headlines/nhl/")
rss("CBS Sports CFB", "https://www.cbssports.com/rss/headlines/college-football/")
rss("Yahoo NFL", "https://sports.yahoo.com/nfl/rss/")
rss("Yahoo NBA", "https://sports.yahoo.com/nba/rss/")
rss("ProFootballTalk", "https://www.nbcsports.com/profootballtalk.rss")
rss("ProFootballTalk alt", "https://profootballtalk.nbcsports.com/feed/")
rss("RotoWire NFL", "https://www.rotowire.com/rss/news.php?sport=NFL")
rss("RotoWire NBA", "https://www.rotowire.com/rss/news.php?sport=NBA")
rss("RotoWire NHL", "https://www.rotowire.com/rss/news.php?sport=NHL")
rss("RotoWire CFB", "https://www.rotowire.com/rss/news.php?sport=CFB")
rss("NFL.com", "https://www.nfl.com/rss/rsslanding?searchString=home")
rss("Bleacher/BR NFL", "https://bleacherreport.com/articles/feed?tag_id=16")
