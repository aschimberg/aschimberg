"""Closing line value (CLV): did the number we picked beat the closing number?

Bet number = the line (or moneyline) when the model first took that side; if
the side flips, it resets. Close = the last pre-kickoff number (NFL: nflverse's
closing line). CLV is the standard skill signal for picks: it becomes
meaningful within a few hundred picks, long before win-loss records do.

Spreads: points from the pick side's view (took -3, closed -5 -> +2.0).
Moneylines: expected return of the price we got at the closing no-vig odds
(took +120 when the fair close was 50% -> +10%).
"""
from .odds import american_to_decimal, american_to_prob, no_vig


def carry_bet(new, prev, now_iso, value, first_seen=None):
    """Set new['bet_line'/'bet_at'], keeping the original number while the side holds.

    first_seen: (timestamp, value) of the earliest snapshot we logged, used for
    picks logged before CLV tracking existed.
    """
    side = new.get("ats_side")
    if not side or value is None:
        return new
    if prev and prev.get("ats_side") == side:
        if prev.get("bet_line") is not None:
            new["bet_line"], new["bet_at"] = prev["bet_line"], prev.get("bet_at")
            if prev.get("bet_src"):
                new["bet_src"] = prev["bet_src"]
            return new
        if first_seen and first_seen[1] is not None:
            new["bet_line"], new["bet_at"], new["bet_src"] = first_seen[1], first_seen[0], "first seen"
            return new
    new["bet_line"], new["bet_at"] = value, now_iso
    return new


def spread_clv(side, bet_spread, close_spread):
    """Home-perspective spreads -> points of CLV for the pick side."""
    sign = 1 if side == "home" else -1
    return round(sign * (bet_spread - close_spread), 1)


def ml_clv(side, bet_odds, close_home, close_away):
    """Expected return of bet_odds at the closing no-vig probability."""
    ph, pa = no_vig(american_to_prob(close_home), american_to_prob(close_away))
    p = ph if side == "home" else pa
    return round(american_to_decimal(bet_odds) * p - 1, 4)


def finalize_spread(p, close_spread=None, cover_prob=None):
    """Record close and CLV on a locked spread pick (no-op for backtests/legacy)."""
    if p.get("backtest") or p.get("bet_line") is None or not p.get("ats_side"):
        return p
    close = close_spread if close_spread is not None else p.get("line")
    if close is None:
        return p
    p["close_line"] = close
    p["clv"] = spread_clv(p["ats_side"], p["bet_line"], close)
    if cover_prob is not None:
        # key-number aware: cover probability at our number minus at the close,
        # both priced on the closing market's margin distribution
        side = p["ats_side"]
        p["clv_prob"] = round(cover_prob(side, p["bet_line"], close) - cover_prob(side, close, close), 4)
    return p


def finalize_ml(p):
    if p.get("backtest") or p.get("bet_line") is None or not p.get("ats_side"):
        return p
    if p.get("ml_home") is None or p.get("ml_away") is None:
        return p
    p["close_line"] = p["ml_home"] if p["ats_side"] == "home" else p["ml_away"]
    p["clv"] = ml_clv(p["ats_side"], p["bet_line"], p["ml_home"], p["ml_away"])
    return p


def backfill(picks, first_seen_of, kind="spread"):
    """Give already-graded legacy picks a bet number from the first snapshot we logged."""
    n = 0
    for gid, p in picks.items():
        if p.get("backtest") or p.get("bet_line") is not None or not p.get("result") or not p.get("ats_side"):
            continue
        fs = first_seen_of(gid, p)
        if not fs or fs[1] is None:
            continue
        p["bet_line"], p["bet_at"], p["bet_src"] = fs[1], fs[0], "first seen"
        if kind == "spread":
            finalize_spread(p)
        else:
            finalize_ml(p)
        n += 1
    return n


def summary(picks, min_edge=None):
    rows = [p for p in picks if p.get("clv") is not None and (min_edge is None or (p.get("edge") or 0) >= min_edge)]
    if not rows:
        return {"n": 0, "avg": None, "beat": 0, "same": 0, "worse": 0, "pct_beat": None}
    beat = sum(1 for p in rows if p["clv"] > 0)
    same = sum(1 for p in rows if p["clv"] == 0)
    return {"n": len(rows), "avg": round(sum(p["clv"] for p in rows) / len(rows), 3), "beat": beat, "same": same,
            "worse": len(rows) - beat - same, "pct_beat": round(beat / len(rows), 3)}
