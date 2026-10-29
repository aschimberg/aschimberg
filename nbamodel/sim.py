"""Monte Carlo game simulation for one NBA matchup.

Each run:
1. Lineup: every player with a known status (injury report live, box score
   in backtests) is drawn in or out from his chance to play. His value moves
   the margin by value x (played - recent availability), as in players.py,
   and TOTAL_INJURY_SHARE of each team's net comes off the total.
2. Pace: possessions ~ Normal(projected pace, sd_p). Shared by both teams,
   which is what correlates the two scores.
3. Scoring: each team's points per possession ~ Normal(mean, sd_e); points =
   possessions x PPP, rounded.
4. End of regulation: a one- or two-point game is pulled to a tie with
   probability TIE_PULL (late fouling and tying threes), then overtime
   periods (5 min, about 10.4 possessions each, per-possession sd 1.1) until
   someone wins.

sd_p and sd_e are set per game so the score spread matches what NBA games do
around the closing line (margin SD MARGIN_SD, total SD TOTAL_SD). Results are
deterministic per game (seeded by game id).
"""
import math
import random
import zlib

from . import config


def _seed(key):
    return zlib.crc32(str(key).encode()) & 0xFFFFFFFF


def run(base_margin, base_total, poss, roster, n=None, seed=0, margin_shift=0.0, total_shift=0.0):
    """Simulate n games. roster: [{'side', 'value', 'p_play', 'recent', ...}].

    Returns {'margins': [...], 'totals': [...], 'played': [[0/1 per roster entry] per run]}
    with margins home minus away. margin_shift/total_shift move the means
    (used to price at the market-anchored line with the same random draws).
    """
    n = n or config.SIMS
    rng = random.Random(_seed(seed))
    msd, tsd = config.MARGIN_SD, config.TOTAL_SD
    poss = max(poss, 80.0)
    margins, totals, played, ots = [], [], [], []
    # overtime adds ~23 points to OT games; take that variance out of regulation
    # so the full-game total spread still matches TOTAL_SD
    reg_tsd2 = max(tsd * tsd - config.OT_TOTAL_VAR, msd * msd + 1.0)
    share = config.TOTAL_INJURY_SHARE
    cap = config.TEAM_ADJ_MAX
    for _ in range(n):
        adj = {"home": 0.0, "away": 0.0}
        flags = []
        for r in roster:
            p = r["p_play"]
            on = 1 if (p >= 1 or (p > 0 and rng.random() < p)) else 0
            flags.append(on)
            adj[r["side"]] += r["value"] * (on - r["recent"])
        ah = max(-cap, min(cap, adj["home"]))
        aa = max(-cap, min(cap, adj["away"]))
        m = base_margin + margin_shift + ah - aa
        t = base_total + total_shift + share * (ah + aa) - config.OT_TOTAL_MEAN
        hp, ap = (t + m) / 2, (t - m) / 2
        sd_e = msd / (poss * math.sqrt(2))
        sd_p = math.sqrt(reg_tsd2 - msd * msd) / max((hp + ap) / poss, 0.5)
        ps = max(70.0, rng.gauss(poss, sd_p))
        h = round(ps * (hp / poss + rng.gauss(0, sd_e)))
        a = round(ps * (ap / poss + rng.gauss(0, sd_e)))
        if 0 < abs(h - a) <= 2 and rng.random() < config.TIE_PULL:
            h = a = max(h, a)
        ot_poss = ps * 5 / 48
        ots.append(h == a)
        while h == a:
            h += max(0, round(rng.gauss(ot_poss * hp / poss, 1.1 * math.sqrt(ot_poss))))
            a += max(0, round(rng.gauss(ot_poss * ap / poss, 1.1 * math.sqrt(ot_poss))))
        margins.append(h - a)
        totals.append(h + a)
        played.append(flags)
    return {"margins": margins, "totals": totals, "played": played, "ot": ots}


def summary(res, roster):
    """Means, win chance, OT-free stats and how the margin moves with each uncertain player."""
    ms, ts = res["margins"], res["totals"]
    n = len(ms)
    out = {"n": n, "margin": sum(ms) / n, "total": sum(ts) / n,
           "home_wp": sum(1 for m in ms if m > 0) / n, "ot": sum(res.get("ot") or []) / n, "scenarios": []}
    for i, r in enumerate(roster):
        if not (0 < r["p_play"] < 1) or abs(r["value"]) < 0.5:
            continue
        on = [ms[k] for k in range(n) if res["played"][k][i]]
        off = [ms[k] for k in range(n) if not res["played"][k][i]]
        if on and off:
            sign = 1 if r["side"] == "home" else -1
            out["scenarios"].append({"name": r["name"], "side": r["side"], "p_play": r["p_play"],
                                     "with": round(sign * sum(on) / len(on), 1), "without": round(sign * sum(off) / len(off), 1)})
    out["scenarios"].sort(key=lambda s: -abs(s["with"] - s["without"]))
    return out


def cover(res, home_spread):
    """(P home covers, P push, P away covers) for a home-perspective spread."""
    n = len(res["margins"])
    hc = sum(1 for m in res["margins"] if m + home_spread > 0) / n
    push = sum(1 for m in res["margins"] if m + home_spread == 0) / n
    return hc, push, 1 - hc - push


def over_under(res, line):
    """(P over, P push, P under)."""
    n = len(res["totals"])
    over = sum(1 for t in res["totals"] if t > line) / n
    push = sum(1 for t in res["totals"] if t == line) / n
    return over, push, 1 - over - push
