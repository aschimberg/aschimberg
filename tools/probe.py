"""Diagnostic: summarize what a few external data sources serve (run from Actions).

    python tools/probe.py
Prints status, robots.txt, terms links, splits pages and API endpoints found in
pages and their JS bundles. Read-only; nothing is saved.
"""
import re
import sys
import urllib.request
from urllib.parse import urljoin, urlparse

UA = {"User-Agent": "Mozilla/5.0 (personal sports model; probe)", "Accept": "*/*"}


def get(url, limit=3_000_000):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read(limit)
            return r.status, r.headers.get("content-type", ""), body.decode("utf-8", "replace"), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), e.read(20000).decode("utf-8", "replace"), url
    except Exception as e:
        return None, "", f"ERR {e}", url


def links(html, base):
    return sorted({urljoin(base, h) for h in re.findall(r'href=["\']([^"\'#]+)', html)})


def endpoints(text):
    pat = r'["\'`](https?://[^"\'`\s]{6,200}|/api/[^"\'`\s]{1,200}|/_next/data/[^"\'`\s]{1,200})["\'`]'
    return sorted({m for m in re.findall(pat, text) if re.search(r"api|split|public|betting|odds|consensus|graphql|json", m, re.I)})


def section(t):
    print("\n" + "=" * 20 + " " + t + " " + "=" * 20, flush=True)


def site(root, keywords):
    section(root)
    st, ct, body, final = get(root)
    print("status", st, ct, "final", final, "bytes", len(body))
    print("title", (re.search(r"<title>(.*?)</title>", body, re.S) or [None, ""])[1].strip()[:200])
    st, _, robots, _ = get(urljoin(root, "/robots.txt"))
    print("--- robots.txt", st)
    print(robots[:3000])
    ls = links(body, final)
    host = urlparse(final).netloc
    print("--- links of interest")
    interesting = [l for l in ls if re.search(keywords, l, re.I)]
    for l in interesting[:80]:
        print(" ", l)
    print("--- endpoints on home page", endpoints(body)[:60])
    scripts = [urljoin(final, s) for s in re.findall(r'<script[^>]+src=["\']([^"\']+)', body)]
    print("--- scripts", len(scripts))
    pages = [l for l in interesting if urlparse(l).netloc.endswith(host.replace("www.", "")) and re.search(r"split|public|consensus|trend", l, re.I)][:6]
    for p in pages:
        st, ct, b, f = get(p)
        print(f"\n--- page {p} -> {st} {ct} {len(b)} bytes")
        txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", b, flags=re.S)
        txt = re.sub(r"<[^>]+>", " ", txt)
        txt = re.sub(r"\s+", " ", txt)
        i = max(0, txt.lower().find("%") - 600)
        print("text sample:", txt[i:i + 1500])
        print("endpoints:", endpoints(b)[:40])
        nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', b, re.S)
        if nd:
            print("__NEXT_DATA__ sample:", nd.group(1)[:1500])
        scripts += [urljoin(f, s) for s in re.findall(r'<script[^>]+src=["\']([^"\']+)', b)]
    seen = set()
    for s in scripts:
        if s in seen or len(seen) >= 25:
            continue
        seen.add(s)
        st, _, js, _ = get(s)
        eps = endpoints(js)
        if eps:
            print(f"--- js {s} ({len(js)} bytes): {eps[:30]}")
    for t in [l for l in ls if re.search(r"terms|tos|legal|conditions", l, re.I)][:3]:
        st, _, b, _ = get(t)
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", " ", b, flags=re.S)))
        print(f"\n--- terms {t} -> {st}")
        for kw in ("scrap", "robot", "spider", "automat", "crawl", "personal", "non-commercial", "noncommercial", "data mining", "extract"):
            for m in re.finditer(kw, txt, re.I):
                print(f"  [{kw}] ...{txt[max(0, m.start() - 250):m.start() + 250]}...")
                break


def heads(urls):
    section("HEAD checks")
    for u in urls:
        st, ct, b, f = get(u, limit=400)
        print(st, ct, u, "->", b[:160].replace("\n", " | "))


if __name__ == "__main__":
    site("https://www.thebettinginsider.com/", r"split|public|betting|consensus|trend|terms|tos|legal|privacy|nfl|nhl|ncaaf|college|api")
    site("https://moneypuck.com/data.htm", r"\.csv|\.zip|download|seasonSummary|goalie|team")
    heads([
        "https://moneypuck.com/moneypuck/playerData/seasonSummary/2025/regular/teams.csv",
        "https://moneypuck.com/moneypuck/playerData/seasonSummary/2025/regular/goalies.csv",
        "https://moneypuck.com/moneypuck/playerData/seasonSummary/2026/regular/teams.csv",
        "https://moneypuck.com/moneypuck/playerData/seasonSummary/2026/regular/goalies.csv",
        "https://moneypuck.com/moneypuck/playerData/careers/gameByGame/all_teams.csv",
        "https://peter-tanner.com/moneypuck/downloads/shots_2025.zip",
    ])
    sys.exit(0)
