"""Anytime TD props (NFL): pricing, best bets, grading.

Each player gets an expected number of scores in the game, lam. P(anytime) =
1 - exp(-lam) (Poisson). With a market price, EV = p * decimal - 1, and best
bets are ranked by the Kelly fraction EV / (decimal - 1): it mixes edge and
probability, so a +12% edge on a 40% scorer outranks a +15% edge on a 6%
long shot. With a market price the model is blended with the books'
consensus first (see price), and only bets with EV >= min_ev, p >= min_prob,
a usage history and a model number near the market's qualify.
"""
import math
import re
import unicodedata

from .odds import american_to_decimal, prob_to_american


def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def short_key(s):
    """'Josh Allen' / 'J.Allen' -> 'jallen'."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s).replace(".", ". ")
    parts = [re.sub(r"[^a-z]", "", p) for p in s.split()]
    parts = [p for p in parts if p]
    return (parts[0][:1] + parts[-1]) if parts else ""


def anytime_prob(lam):
    return 1 - math.exp(-max(0.0, lam))


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def blend(p_model, p_market, model_weight):
    """Model and market probability averaged in log-odds space."""
    z = model_weight * _logit(p_model) + (1 - model_weight) * _logit(p_market)
    return 1 / (1 + math.exp(-z))


def price(players, market=None, model_weight=0.4):
    """players: [{'name','team','lam',...}] -> adds p, fair odds and (if priced) odds/book/ev/kelly.

    market: {norm_name: {'price': american, 'book': str, 'n_books': int, 'mkt_prob': float}}

    With a market price, p is the model blended with the books' consensus
    (model_weight of the log-odds from the model): anytime-TD markets are
    sharp enough that the model's raw number, especially for a backup with
    little usage history, shouldn't be bet against six books on its own.
    p_model keeps the raw model number.
    """
    out = []
    for pl in players:
        pm = anytime_prob(pl["lam"])
        row = dict(pl, p=round(pm, 3), p_model=round(pm, 3))
        m = (market or {}).get(norm_name(pl["name"]))
        if m:
            fair = m.get("fair_prob", m.get("mkt_prob"))  # margin-free when devig() has run
            p = blend(pm, fair, model_weight) if fair else pm
            dec = american_to_decimal(m["price"])
            ev = p * dec - 1
            row.update(p=round(p, 3), odds=m["price"], book=m.get("book"), n_books=m.get("n_books"), mkt_prob=fair,
                       ev=round(ev, 4), kelly=round(ev / (dec - 1), 4) if dec > 1 else None)
        row["fair"] = round(prob_to_american(row["p"])) if 0 < row["p"] < 1 else None
        out.append(row)
    return out


def best_bets(priced, n=3, min_ev=0.03, min_prob=0.15, min_games=2, max_ratio=2.5):
    """Top n per game: qualifying bets by Kelly; if none qualify, the top n by probability.

    A bet needs EV >= min_ev and p >= min_prob (both on the market-blended
    probability), at least min_games of usage history, and a model number
    within max_ratio of the market's (a bigger gap almost always means the
    model is missing something, like a stale depth chart).
    """
    def ok(r):
        if r.get("ev") is None or r["ev"] < min_ev or r["p"] < min_prob:
            return False
        if r.get("games") is not None and r["games"] < min_games:
            return False
        m = r.get("mkt_prob")
        return not m or (r["p_model"] / m <= max_ratio and m / r["p_model"] <= max_ratio)

    bets = sorted((r for r in priced if ok(r)), key=lambda r: -(r["kelly"] or 0))
    if bets:
        return [dict(r, bet=True) for r in bets[:n]]
    have_prices = any(r.get("odds") is not None for r in priced)
    top = sorted(priced, key=lambda r: -r["p"])[:n]
    return [dict(r, bet=False, note="no value at market price" if have_prices else "no market price yet") for r in top]


def parse_market(event, market_key):
    """Odds API event odds -> {norm_name: best price across books + consensus implied prob}."""
    by = {}
    for b in (event or {}).get("bookmakers", []):
        for mk in b.get("markets", []):
            if mk.get("key") != market_key:
                continue
            for o in mk.get("outcomes", []):
                if str(o.get("name", "")).lower() in ("no", "under"):
                    continue
                player = o.get("description") or o.get("name")
                if not player or o.get("price") is None:
                    continue
                by.setdefault(norm_name(player), []).append((float(o["price"]), b.get("title", b.get("key"))))
    out = {}
    for k, rows in by.items():
        best = max(rows, key=lambda r: american_to_decimal(r[0]))
        probs = sorted(1 / american_to_decimal(r[0]) for r in rows)
        mid = len(probs) // 2
        med = probs[mid] if len(probs) % 2 else (probs[mid - 1] + probs[mid]) / 2
        out[k] = {"price": best[0], "book": best[1], "n_books": len(rows), "mkt_prob": round(med, 3)}
    return out


def devig(market, expected_tds, default_scale=0.85):
    """Strip the bookmaker's margin from 'Yes'-only anytime prices.

    Books post only the Yes side, so their implied chances add up to more
    touchdowns than the game will produce. Each player's implied scoring rate
    (-ln(1-p)) is scaled so the game's total matches expected_tds (from the
    betting total); without a total, a typical margin is assumed. Never scales
    up. Adds 'fair_prob' to each entry and returns the scale used.
    """
    if not market:
        return None
    lam = {k: -math.log(max(1e-6, 1 - v["mkt_prob"])) for k, v in market.items()}
    total = sum(lam.values())
    scale = min(1.0, expected_tds / total) if expected_tds and total else default_scale
    scale = max(scale, 0.6)
    for k, v in market.items():
        v["fair_prob"] = round(1 - math.exp(-lam[k] * scale), 3)
    return round(scale, 3)


def grade(bets, scorer_ids, scorer_names=()):
    """Mark each best bet W/L given who scored (ids, plus normalized names as a fallback)."""
    ids = {str(i) for i in scorer_ids}
    names = {norm_name(n) for n in scorer_names} | {short_key(n) for n in scorer_names}
    for b in bets:
        hit = (b.get("pid") is not None and str(b["pid"]) in ids) or norm_name(b["name"]) in names or short_key(b["name"]) in names
        b["result"] = "W" if hit else "L"
        if b.get("bet") and b.get("odds") is not None:
            b["units"] = round(american_to_decimal(b["odds"]) - 1, 3) if hit else -1.0
    return bets


def record(picks, key="props"):
    """Scorer-prop record across graded games.

    Top level: logged best bets (bet=True) W-L and units. Also every listed
    scorer pick ('all'), the model's top scorer per game ('top'), and per-game
    averages: scorers hit vs the model's expected (sum of probabilities).
    """
    w = l_ = 0
    units = 0.0
    n_lean = n_hit = 0
    games = hits = 0
    exp = 0.0
    n_listed = 0
    top = {"W": 0, "L": 0}
    for p in picks:
        rows = [b for b in p.get(key) or [] if b.get("result") is not None]
        if not rows:
            continue
        games += 1
        n_listed += len(rows)
        hits += sum(b["result"] == "W" for b in rows)
        exp += sum(b.get("p") or 0.0 for b in rows)
        best = max(rows, key=lambda b: b.get("p") or 0.0)
        top[best["result"]] += 1
        for b in rows:
            if b.get("bet"):
                w += b["result"] == "W"
                l_ += b["result"] == "L"
                units += b.get("units") or 0.0
            else:
                n_lean += 1
                n_hit += b["result"] == "W"
    pct = lambda a, b: round(a / (a + b), 3) if a + b else None
    return {"W": w, "L": l_, "P": 0, "pct": pct(w, l_), "units": round(units, 2),
            "roi": round(units / (w + l_), 3) if w + l_ else None,
            "top_prob_hits": n_hit, "top_prob_n": n_lean,
            "all": {"W": hits, "L": n_listed - hits, "P": 0, "pct": pct(hits, n_listed - hits)},
            "top": {"W": top["W"], "L": top["L"], "P": 0, "pct": pct(top["W"], top["L"])},
            "games": games, "picks_per_game": round(n_listed / games, 2) if games else None,
            "hits_per_game": round(hits / games, 2) if games else None,
            "exp_per_game": round(exp / games, 2) if games else None}


def briefing_lines(games, label, matchup, limit=6):
    """Best-bet scorer props across today's games for an ntfy briefing."""
    rows = [(b, c) for c in games for b in ((c.get("pick") or {}).get("props") or []) if b.get("bet")]
    rows.sort(key=lambda x: -(x[0].get("kelly") or 0))
    if not rows:
        return []
    out = ["", f"**🎯 {label} best bets**"]
    for b, c in rows[:limit]:
        odds = f"{'+' if b['odds'] > 0 else ''}{round(b['odds'])}"
        warn = " ⚠ injury news" if (b.get("news") or {}).get("flag") == "warn" else ""
        out.append(f"{b['name']} ({b['team']}) {odds} {b.get('book') or ''} — {round(b['p'] * 100)}% to score, "
                   f"EV {b['ev'] * 100:+.1f}% ({matchup(c)}){warn}")
    return out
