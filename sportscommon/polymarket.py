"""Polymarket US game-winner prices vs the model (public market data, no key).

Polymarket US (a CFTC-regulated exchange) lists one winner contract per game.
The "long" side is the away team; buying the home team is selling long, so the
home price is 1 - the best bid. Contracts pay $1; a tie with no winner settles
at $0.50.

Each game gets its best bid/ask and an EV per $1 staked at the ask, after the
taker fee (theta x p x (1 - p) per contract; theta 0.095 below $10K of 30-day
volume). The win chance is the market-anchored one from the pick pricing (the
model's simulated games re-centered on the anchored spread), not the raw
model, so a price only shows value where the spread pick would too.

A side with enough EV is logged as a paper bet at the first price it showed
value, carried like a filled order until the game starts, then graded in units
(1 unit staked per bet) with closing line value (closing ask - entry price).

Env: POLYMARKET=off disables; POLY_TAKER_THETA, POLY_MIN_EV, POLY_MAX_SPREAD,
POLY_GATEWAY.
"""
import os
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import anchor as anc
from .http import fetch_json

GATEWAY = os.environ.get("POLY_GATEWAY", "https://gateway.polymarket.us")
THETA = float(os.environ.get("POLY_TAKER_THETA", 0.095))
MIN_EV = float(os.environ.get("POLY_MIN_EV", 0.02))  # EV per $1 at the ask, after fees, to log a bet
MAX_SPREAD = float(os.environ.get("POLY_MAX_SPREAD", 0.05))  # wider bid/ask = too thin to trust
MATCH_HOURS = 6


def enabled():
    return os.environ.get("POLYMARKET", "on").lower() not in ("off", "0", "false", "no")


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(path, query=None):
    url = GATEWAY + path + ("?" + urllib.parse.urlencode(query) if query else "")
    return fetch_json(url, retries=2, timeout=20)


def _px(v):
    try:
        return float((v or {}).get("value"))
    except (TypeError, ValueError):
        return None


def _team(side):
    t = side.get("team") or {}
    return {"name": t.get("name") or side.get("description"), "abbr": (t.get("displayAbbreviation") or t.get("abbreviation") or "").upper(),
            "safe": t.get("safeName") or side.get("description"), "alias": t.get("alias")}


def parse_event(e):
    """Winner market of one game event -> {slug, market, start, away, home}, or None."""
    for m in e.get("markets") or []:
        kind = m.get("sportsMarketType") or ""
        if not (kind.endswith("_full_game_winner") or m.get("marketType") == "moneyline") or m.get("closed"):
            continue
        sides = m.get("marketSides") or []
        long_ = next((s for s in sides if s.get("long")), None)
        short = next((s for s in sides if s.get("long") is False), None)
        if not long_ or not short:
            continue
        # long is the away team in leagues listed away-first; trust the team's own ordering when given
        if (long_.get("team") or {}).get("ordering") == "home":
            home, away, home_long = long_, short, True
        else:
            home, away, home_long = short, long_, False
        return {"slug": e.get("slug"), "market": m["slug"], "start": e.get("startTime") or m.get("gameStartTime"),
                "away": _team(away), "home": _team(home), "home_long": home_long}
    return None


def board(league, now, hours, log=print, getter=None):
    """Game winner markets for a league starting in the next `hours` (one paged events call)."""
    get = getter or _get
    q = {"tagSlug": league, "closed": "false", "limit": 100,
         "startTimeMin": _iso(now - timedelta(hours=4)), "startTimeMax": _iso(now + timedelta(hours=hours))}
    out, offset = [], 0
    while True:
        evs = (get("/v1/events", {**q, "offset": offset}) or {}).get("events") or []
        out += [g for g in (parse_event(e) for e in evs) if g]
        if len(evs) < q["limit"] or offset >= 500:
            break
        offset += q["limit"]
    log(f"Polymarket {league}: {len(out)} game markets")
    return out


def quote(game, getter=None):
    """Best bid/ask for both teams, in dollars per $1 contract."""
    md = ((getter or _get)(f"/v1/markets/{game['market']}/bbo") or {}).get("marketData") or {}
    bid, ask = _px(md.get("bestBid")), _px(md.get("bestAsk"))
    if bid is None or ask is None or not 0 < bid < ask < 1 or md.get("state", "MARKET_STATE_OPEN") != "MARKET_STATE_OPEN":
        return None
    long_side, short_side = ("home", "away") if game["home_long"] else ("away", "home")
    try:
        oi = float(md.get("openInterest") or 0)
    except ValueError:
        oi = None
    return {long_side: {"ask": ask, "bid": bid}, short_side: {"ask": round(1 - bid, 4), "bid": round(1 - ask, 4)},
            "last": _px(md.get("lastTradePx")), "spread": round(ask - bid, 4), "oi": oi}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _words(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def _same_team(pm, name):
    n = _norm(name)
    if not n:
        return False
    for k in ("name", "alias"):
        if pm.get(k) and _norm(pm[k]) == n:
            return True
    words, safe = _words(name), _words(pm.get("safe"))
    return bool(safe) and words[-len(safe):] == safe  # "Los Angeles Lakers" ends with the word "Lakers" ("Hornets" isn't "Nets")


def find(games, home, away, start):
    """The market for home vs away starting within MATCH_HOURS of start (ISO), or None."""
    t0 = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
    for g in games:
        if not (_same_team(g["home"], home) and _same_team(g["away"], away)):
            continue
        if t0 and g.get("start"):
            t1 = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
            if abs((t1 - t0).total_seconds()) > MATCH_HOURS * 3600:
                continue
        return g
    return None


def cost(price, theta=None):
    """Dollars paid per contract at `price`, taker fee included."""
    th = THETA if theta is None else theta
    return price + th * price * (1 - price)


def ev(p_win, price, theta=None):
    """Profit per $1 staked buying at `price` with win chance p_win (ties count half)."""
    return p_win / cost(price, theta) - 1


def price(q, p_home, names, game=None, now=None, bet_ok=True):
    """Card block: both sides' prices, the market's implied chance, model chance and EV; value side if any
    (none when bet_ok is False, e.g. early season while picks are capped at lean)."""
    if not q:
        return None
    blk = {"market": game["market"] if game else None, "event": game["slug"] if game else None,
           "at": _iso(now) if now else None, "spread": q["spread"], "oi": q["oi"], "last": q.get("last"),
           "mid_home": round((q["home"]["bid"] + q["home"]["ask"]) / 2, 3), "value": None}
    for side in ("home", "away"):
        s = q[side]
        p = None if p_home is None else (p_home if side == "home" else 1 - p_home)
        blk[side] = {"team": names[side], "ask": s["ask"], "bid": s["bid"], "p": None if p is None else round(p, 3),
                     "ev": None if p is None else round(ev(p, s["ask"]), 4)}
    if bet_ok and p_home is not None and q["spread"] <= MAX_SPREAD:
        side = max(("home", "away"), key=lambda k: blk[k]["ev"])
        b = blk[side]
        if b["ev"] >= MIN_EV:
            blk["value"] = {"side": side, "team": b["team"], "price": b["ask"], "cost": round(cost(b["ask"]), 4),
                            "p": b["p"], "ev": b["ev"], "tier": anc.tier(b["ev"])}
    return blk


def carry(entry, prev, blk, now_iso):
    """Attach the latest quote to a pick, and keep (or open) the paper bet at its first value price."""
    if blk:
        entry["poly"] = blk
    elif prev and prev.get("poly"):
        entry["poly"] = prev["poly"]
    bet = (prev or {}).get("poly_bet")
    if not bet and blk and blk.get("value"):
        bet = {**blk["value"], "at": now_iso, "market": blk.get("market")}
    if bet:
        entry["poly_bet"] = bet


def grade(p, home_pts, away_pts):
    """Settle a pick's paper bet: W/L (tie = P at $0.50), profit per unit staked and CLV vs the closing ask."""
    bet = p.get("poly_bet")
    if not bet or bet.get("result") or home_pts is None or away_pts is None:
        return
    won = home_pts > away_pts if bet["side"] == "home" else away_pts > home_pts
    payout = 0.5 if home_pts == away_pts else (1.0 if won else 0.0)
    bet["result"] = "P" if home_pts == away_pts else ("W" if won else "L")
    bet["profit"] = round(payout / bet["cost"] - 1, 4)
    close = ((p.get("poly") or {}).get(bet["side"]) or {}).get("ask")
    if close is not None:
        bet["close"] = close
        bet["clv"] = round(close - bet["price"], 4)


def summarize(picks, season=None):
    """Paper record of logged bets: W-L-P, units, ROI, and average CLV in cents."""
    bets = [p["poly_bet"] for p in picks.values()
            if p.get("poly_bet") and (season is None or p.get("season") == season)]
    done = [b for b in bets if b.get("result")]
    clvs = [b["clv"] for b in done if b.get("clv") is not None]
    units = sum(b["profit"] for b in done)
    return {"open": len(bets) - len(done), "W": sum(b["result"] == "W" for b in done),
            "L": sum(b["result"] == "L" for b in done), "P": sum(b["result"] == "P" for b in done),
            "units": round(units, 2), "roi": round(units / len(done), 4) if done else None,
            "avg_ev": round(sum(b["ev"] for b in done) / len(done), 4) if done else None,
            "clv": round(sum(clvs) / len(clvs), 4) if clvs else None, "beat_close": sum(c > 0 for c in clvs),
            "n_clv": len(clvs), "theta": THETA, "min_ev": MIN_EV}


def fetch_quotes(league, games, now, hours, log=print, getter=None):
    """{game key: (market game, quote)} for games = [(key, home name, away name, start ISO)]."""
    if not enabled() or not games:
        return {}
    try:
        mk = board(league, now, hours, log=log, getter=getter)
    except Exception as e:  # market data is extra; never fail a refresh over it
        log(f"Polymarket {league}: unavailable ({e})")
        return {}
    out = {}
    for key, home, away, start in games:
        g = find(mk, home, away, start)
        if not g:
            continue
        try:
            q = quote(g, getter=getter)
        except Exception as e:
            log(f"Polymarket {league} {g['market']}: no quote ({e})")
            continue
        if q:
            out[key] = (g, q)
    log(f"Polymarket {league}: priced {len(out)} of {len(games)} games")
    return out
