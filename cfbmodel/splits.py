"""Public bet % / money % feed (swappable) and sharp/public divergence flags.

No free source we checked grants permission for automated collection of bet
splits (the popular public pages forbid scraping in their terms), so the
default provider is "none". Plug in a licensed feed by setting
SPLITS_PROVIDER:

  none       - no splits; the UI shows "feed not configured"
  file       - read data/splits_manual.json (normalized schema below)
  http_json  - GET SPLITS_URL (optional Bearer SPLITS_API_KEY) returning the
               normalized schema; point it at your own adapter for a licensed
               vendor (e.g. Sportradar Betting Splits)

Normalized schema (a list, or {"source": ..., "games": [...]}):
  {"home": "Texas", "away": "Oklahoma",          # CFBD team names preferred
   "game_id": 401234567,                          # optional, matched first
   "spread": {"home_bets": 62, "home_money": 41}, # away = 100 - home
   "total":  {"over_bets": 70, "over_money": 55},
   "moneyline": {"home_bets": 58, "home_money": 66},
   "updated": "2026-10-10T13:00:00Z"}
"""
import json
import re
import urllib.request

from . import config


def norm_team(name):
    s = (name or "").lower().replace("&", "and")
    s = re.sub(r"\bst\.?\b", "state", s)
    return re.sub(r"[^a-z0-9]", "", s)


class SplitsProvider:
    name = "none"
    source_label = None

    def fetch(self):
        return []


class FileProvider(SplitsProvider):
    name = "file"

    def __init__(self, path=None):
        self.path = path or config.SPLITS_FILE

    def fetch(self):
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text())
        if isinstance(payload, dict):
            self.source_label = payload.get("source")
            return payload.get("games", [])
        return payload


class HttpJsonProvider(SplitsProvider):
    name = "http_json"

    def __init__(self, url=None, api_key=None):
        self.url = url or config.SPLITS_URL
        self.api_key = api_key or config.SPLITS_API_KEY

    def fetch(self):
        if not self.url:
            return []
        headers = {"Accept": "application/json", "User-Agent": "cfb-model-site/1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if isinstance(payload, dict):
            self.source_label = payload.get("source")
            return payload.get("games", [])
        return payload


PROVIDERS = {"none": SplitsProvider, "file": FileProvider, "http_json": HttpJsonProvider}


def get_provider(name=None):
    return PROVIDERS.get(name or config.SPLITS_PROVIDER, SplitsProvider)()


def _pair(home_val):
    if home_val is None:
        return None
    return round(float(home_val), 1), round(100 - float(home_val), 1)


def analyze(rec, line_move=None):
    """Turn a normalized splits record into per-side numbers + flags.

    line_move: current home spread minus open home spread (negative = moved
    toward home), used for reverse-line-movement detection.
    """
    out = {"updated": rec.get("updated"), "markets": {}, "flags": []}
    for market, keys in (("spread", ("home", "away")), ("moneyline", ("home", "away")), ("total", ("over", "under"))):
        m = rec.get(market) or {}
        first = keys[0]
        bets = _pair(m.get(f"{first}_bets"))
        money = _pair(m.get(f"{first}_money"))
        if not bets and not money:
            continue
        sides = {}
        for i, k in enumerate(keys):
            sides[k] = {"bets": bets[i] if bets else None, "money": money[i] if money else None}
        out["markets"][market] = sides
        for k, v in sides.items():
            if v["bets"] is None or v["money"] is None:
                continue
            gap = v["money"] - v["bets"]
            flag = {"market": market, "side": k, "bets": v["bets"], "money": v["money"], "gap": round(gap, 1)}
            if gap >= config.SHARP_GAP:
                out["flags"].append({"type": "sharp", **flag})
            if v["bets"] >= config.PUBLIC_BETS:
                out["flags"].append({"type": "public", **flag})
    spread = out["markets"].get("spread")
    if spread and line_move and spread["home"]["bets"] is not None:
        majority = "home" if spread["home"]["bets"] > 50 else "away"
        # Home spread moving up (less negative) = line moving toward away team.
        moved_against = (majority == "home" and line_move > 0) or (majority == "away" and line_move < 0)
        if moved_against and abs(line_move) >= 1 and abs(spread[majority]["bets"] - 50) >= 5:
            other = "away" if majority == "home" else "home"
            out["flags"].append(
                {"type": "rlm", "market": "spread", "side": other, "bets": spread[other]["bets"],
                 "money": spread[other]["money"], "gap": 0, "move": line_move}
            )
    out["flags"].sort(key=lambda f: ({"sharp": 0, "rlm": 1, "public": 2}[f["type"]], f["market"] != "spread", -f["gap"]))
    return out


def side_name(side, home, away):
    return {"home": home, "away": away, "over": "Over", "under": "Under"}[side]


def flag_text(flag, home, away):
    """Human-readable flag, e.g. 'Sharp $ on Utah (spread): 30% bets / 52% money'."""
    name = side_name(flag["side"], home, away)
    mkt = {"spread": "ATS", "moneyline": "ML", "total": "total"}[flag["market"]]
    nums = f"{flag['bets']:g}% bets / {flag['money']:g}% $" if flag.get("money") is not None else f"{flag['bets']:g}% bets"
    if flag["type"] == "sharp":
        return f"Sharp $ on {name} {mkt} ({nums})"
    if flag["type"] == "public":
        return f"Public on {name} {mkt} ({nums})"
    return f"Reverse line move toward {name} ({abs(flag['move']):g} pts vs {100 - flag['bets']:g}% of bets)"


def match(games, records):
    """Map CFBD game id -> splits record."""
    by_id = {r["game_id"]: r for r in records if r.get("game_id") is not None}
    by_names = {(norm_team(r.get("home")), norm_team(r.get("away"))): r for r in records}
    out = {}
    for gm in games:
        r = by_id.get(gm["id"])
        if r is None:
            r = by_names.get((norm_team(gm["home"]), norm_team(gm["away"])))
        if r is None:
            # Feed may list neutral-site games with home/away flipped.
            flipped = by_names.get((norm_team(gm["away"]), norm_team(gm["home"])))
            if flipped is not None:
                r = _flip(flipped)
        if r is not None:
            out[gm["id"]] = r
    return out


def _flip(r):
    f = dict(r, home=r.get("away"), away=r.get("home"))
    for market in ("spread", "moneyline"):
        m = r.get(market)
        if m:
            f[market] = {
                k: (100 - v if v is not None else None)
                for k, v in (("home_bets", m.get("home_bets")), ("home_money", m.get("home_money")))
            }
    return f
