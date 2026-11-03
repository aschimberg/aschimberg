"""Monte Carlo game simulation for one NBA matchup, played possession by possession.

Each run:
1. Lineup: every player with a known status (injury report live, box score
   in backtests) is drawn in or out from his chance to play. His value moves
   the margin by value x (played - recent availability), as in players.py,
   and TOTAL_INJURY_SHARE of each team's net comes off the total.
2. The game is played out possession by possession (sportscommon.hoopsim):
   turnovers, fouls and free throws, threes and twos, offensive rebounds,
   a running clock with late-game fouling, and overtime until someone wins.

Results are deterministic per game (seeded by game id).
"""
import zlib

from sportscommon import hoopsim

from . import config


def _seed(key):
    return zlib.crc32(str(key).encode()) & 0xFFFFFFFF


def run(base_margin, base_total, poss, roster, n=None, seed=0, margin_shift=0.0, total_shift=0.0):
    """Simulate n games possession by possession (sportscommon.hoopsim).

    roster: [{'side', 'value', 'p_play', 'recent', ...}]. Each game first draws which uncertain
    players suit up; their values move that game's scoring targets (TOTAL_INJURY_SHARE of the net
    comes off the total), then the game is played out. Returns {'margins', 'totals', 'played', 'ot'}
    with margins home minus away; margin_shift/total_shift move the targets (used to price at the
    market-anchored number with the same random draws).
    """
    n = n or config.SIMS
    m0, t0 = base_margin + margin_shift, base_total + total_shift
    share, cap = config.TOTAL_INJURY_SHARE, config.TEAM_ADJ_MAX

    def draws(rng):
        adj = {"home": 0.0, "away": 0.0}
        flags = []
        for r in roster:
            p = r["p_play"]
            on = 1 if (p >= 1 or (p > 0 and rng.random() < p)) else 0
            flags.append(on)
            adj[r["side"]] += r["value"] * (on - r["recent"])
        ah = max(-cap, min(cap, adj["home"]))
        aa = max(-cap, min(cap, adj["away"]))
        dm, dt = ah - aa, share * (ah + aa)
        return (dt + dm) / 2, (dt - dm) / 2, flags

    res = hoopsim.run("nba", (t0 + m0) / 2, (t0 - m0) / 2, max(poss, 80.0), n, seed=_seed(seed),
                      draws=draws if roster else None)
    if not roster:
        res["played"] = [[] for _ in res["margins"]]
    return res


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
