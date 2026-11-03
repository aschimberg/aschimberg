"""Play-by-play football simulation (NFL and college).

Every snap is played: down, distance, field position and clock. Offenses run or pass (more
passing when behind late, more running when protecting a lead), passes can be completed,
dropped, sacked or intercepted, fumbles happen, fourth downs become punts, field goals (make
chance by distance) or go-for-it tries, touchdowns are followed by an extra point or a
two-point try, and the clock runs or stops by play type, with hurry-up when trailing late,
kneel-downs to close a game, and overtime (NFL: both teams possess, then sudden death, ties
allowed; college: alternating possessions from the 25, two-point tries from the third OT).

Each offense gets an efficiency scale k (yards per play, completion rate, fewer sacks and
turnovers as k rises). k is solved per game so each team's average points match the model's
projection, so the simulation shapes the spread of outcomes (key numbers, late-game swings,
overtime) around the model's number instead of replacing it.
"""
import math
import random
import zlib

from . import parallel

LEAGUES = {
    "nfl": {"quarter": 900, "ot": 600, "kick_start": 70, "pass": 0.58, "run_mean": 4.4, "pass_mean": 11.0,
            "comp": 0.645, "sack": 0.068, "int": 0.022, "fumble": 0.007, "fg_mid": 53.0, "fg_scale": 5.0,
            "punt_net": 41.0, "pat": 0.95, "two": 0.48, "play_secs": 37.0, "college_ot": False, "day_sd": 0.08},
    "cfb": {"quarter": 900, "ot": 0, "kick_start": 75, "pass": 0.50, "run_mean": 4.9, "pass_mean": 12.0,
            "comp": 0.62, "sack": 0.065, "int": 0.025, "fumble": 0.010, "fg_mid": 47.0, "fg_scale": 5.5,
            "punt_net": 39.0, "pat": 0.97, "two": 0.45, "play_secs": 27.0, "college_ot": True, "day_sd": 0.05},
}
PILOT = 2000


def _seed(key):
    return zlib.crc32(str(key).encode()) & 0xFFFFFFFF


class _Off:
    __slots__ = ("run", "pmean", "comp", "sack", "int", "fum")

    def __init__(self, lg, k):
        self.run = lg["run_mean"] * k
        self.pmean = lg["pass_mean"] * k
        self.comp = min(0.85, lg["comp"] * math.sqrt(k))
        self.sack = lg["sack"] / k
        self.int = lg["int"] / k
        self.fum = lg["fumble"] / math.sqrt(k)


def _fg_prob(lg, ytg):
    dist = ytg + 17
    return 1 / (1 + math.exp((dist - lg["fg_mid"]) / lg["fg_scale"]))


def _drive(rng, lg, o, yl, clock, lead, half_end, ot=False):
    """Play one possession from yl yards to the goal. Returns (points, new_yl_for_other_team or None,
    clock). points: 6 for a TD (try handled by caller), 3 FG, -2 safety (scored by the defense),
    0 otherwise. new_yl is where the other team starts if the ball changes hands on a turnover/punt."""
    rand = rng.random
    down, dist = 1, min(10, yl)
    while True:
        if clock <= 0 and not ot:
            return 0, None, clock
        trailing_late = lead < 0 and clock < 300 and half_end
        leading_late = lead > 0 and clock < 300 and half_end
        if leading_late and clock <= 40 * (4 - down) + 2:
            return 0, None, 0  # kneel it out
        # fourth down decision
        if down == 4:
            fgp = _fg_prob(lg, yl)
            must_go = trailing_late and (lead < -3 or clock < 120)
            if not must_go and (dist > 2 or yl > 55 or (dist > 1 and yl > 40)):
                if yl <= 40 and fgp > 0.35:
                    clock -= 5
                    if rand() < fgp:
                        return 3, None, clock
                    return 0, min(80, 100 - yl - 7), clock
                if not must_go:
                    clock -= 8
                    land = yl - lg["punt_net"] - rng.gauss(0, 7)
                    return 0, (80 if land <= 0 else 100 - max(1, int(land))), clock
        hurry = ot or trailing_late or (clock < 120 and half_end is False and lead <= 0)
        p_pass = lg["pass"] + (0.25 if trailing_late else 0) - (0.25 if leading_late else 0) + (0.12 if dist >= 8 else -0.10 if dist <= 2 else 0)
        if rand() < p_pass:
            r = rand()
            if r < o.sack:
                gain, secs, stop = -7, 30, False
            elif r < o.sack + o.int:
                clock -= 8
                spot = yl - int(rng.expovariate(1 / 9.0))
                return 0, (80 if spot <= 0 else max(1, min(99, 100 - spot))), clock
            elif rand() < o.comp:
                gain = 1 + int(rng.expovariate(1 / o.pmean))
                secs, stop = (12 if hurry else lg["play_secs"]), hurry and rand() < 0.5
            else:
                gain, secs, stop = 0, 6, True
        else:
            gain = int(rng.expovariate(1 / o.run)) - 1
            if rand() < 0.03:
                gain += int(rng.expovariate(1 / 15.0))
            secs, stop = (14 if hurry else lg["play_secs"] + (6 if leading_late else 0)), False
        if rand() < o.fum:
            clock -= 6
            spot = yl - max(0, gain)
            return 0, (80 if spot <= 0 else max(1, min(99, 100 - spot))), clock
        clock -= secs * (0.75 + 0.5 * rand()) if not stop else 6
        yl -= gain
        if yl <= 0:
            return 6, None, clock
        if yl >= 100:
            return -2, None, clock
        if gain >= dist:
            down, dist = 1, min(10, yl)
        else:
            down += 1
            dist -= gain
            if down > 4:
                return 0, 100 - yl, clock


def _try(rng, lg, lead_after_td, late):
    """Extra point or two-point try; returns points added."""
    go_two = late and lead_after_td in (-2, -5, -10, 1, 5, -9)
    if go_two:
        return 2 if rng.random() < lg["two"] else 0
    return 1 if rng.random() < lg["pat"] else 0


def play(rng, lg, offs):
    """One game; offs = (home _Off, away _Off). Returns (home, away, went_to_ot)."""
    score = [0, 0]
    first = 0 if rng.random() < 0.5 else 1
    for half in (0, 1):
        clock = 2 * lg["quarter"]
        pos = first if half == 0 else 1 - first
        yl = lg["kick_start"]
        while clock > 0:
            lead = score[pos] - score[1 - pos]
            pts, nxt, clock = _drive(rng, lg, offs[pos], yl, clock, lead, half == 1)
            if pts == 6:
                score[pos] += 6
                score[pos] += _try(rng, lg, score[pos] - score[1 - pos], half == 1 and clock < 600)
                pos, yl = 1 - pos, lg["kick_start"]
            elif pts == 3:
                score[pos] += 3
                pos, yl = 1 - pos, lg["kick_start"]
            elif pts == -2:
                score[1 - pos] += 2
                pos, yl = 1 - pos, 60  # free kick after a safety
            else:
                pos, yl = 1 - pos, (nxt if nxt is not None else lg["kick_start"])
    if score[0] != score[1]:
        return score[0], score[1], False
    return (*_overtime(rng, lg, offs, score), True)


def _overtime(rng, lg, offs, score):
    if lg["college_ot"]:
        for n in range(1, 12):
            got = []
            for pos in (0, 1):
                if n >= 3:
                    got.append(2 if rng.random() < lg["two"] else 0)
                    continue
                pts, _, _ = _drive(rng, lg, offs[pos], 25, 10 ** 6, 0, False, ot=True)
                if pts == 6:
                    pts += 2 if (n == 2 and rng.random() < lg["two"]) else (1 if n == 1 and rng.random() < lg["pat"] else 0)
                got.append(max(pts, 0))
            score[0] += got[0]
            score[1] += got[1]
            if got[0] != got[1]:
                break
        if score[0] == score[1]:
            score[0 if rng.random() < 0.5 else 1] += 2
        return score[0], score[1]
    clock = lg["ot"]
    pos = 0 if rng.random() < 0.5 else 1
    turns = 0
    yl = lg["kick_start"]
    while clock > 0:
        lead = score[pos] - score[1 - pos]
        pts, nxt, clock = _drive(rng, lg, offs[pos], yl, clock, lead, True, ot=True)
        if clock <= 0 and pts == 0:
            break
        if pts == 6:
            score[pos] += 6 + (1 if rng.random() < lg["pat"] else 0)
        elif pts == 3:
            score[pos] += 3
        elif pts == -2:
            score[1 - pos] += 2
        turns += 1
        if turns >= 2 and score[0] != score[1]:
            break
        pos, yl = 1 - pos, (nxt if (nxt is not None and pts == 0) else lg["kick_start"])
    return score[0], score[1]


def _games(lg, n, rng, kh, ka):
    """n games; each game draws a day-to-day form factor per offense (lg['day_sd'], lognormal)."""
    margins, totals, ots = [], [], []
    sd = lg["day_sd"]
    for _ in range(n):
        offs = (_Off(lg, kh * math.exp(rng.gauss(0, sd))), _Off(lg, ka * math.exp(rng.gauss(0, sd))))
        h, a, ot = play(rng, lg, offs)
        margins.append(h - a)
        totals.append(h + a)
        ots.append(ot)
    return margins, totals, ots


_K_CACHE = {}


def _k_guess(league, pts):
    """Starting efficiency scale for a points target, from a small table built once per process."""
    lg = LEAGUES[league]
    table = _K_CACHE.get(league)
    if table is None:
        rng = random.Random(1)
        table = []
        for i in range(12):
            k = 0.55 + 0.1 * i
            m, t, _ = _games(lg, 400, rng, k, k)
            table.append((k, sum(t) / 800))
        _K_CACHE[league] = table
    if pts <= table[0][1]:
        return table[0][0] * max(0.3, pts / max(table[0][1], 1))
    for (k0, p0), (k1, p1) in zip(table, table[1:]):
        if p0 <= pts <= p1:
            return k0 + (k1 - k0) * (pts - p0) / (p1 - p0)
    return table[-1][0] * pts / table[-1][1]


def run(league, home_pts, away_pts, n=20000, seed=0):
    """Simulate n games with each team's average points calibrated to its projection."""
    lg = LEAGUES[league]
    home_pts, away_pts = max(3.0, home_pts), max(3.0, away_pts)
    kh, ka = _k_guess(league, home_pts), _k_guess(league, away_pts)
    base = min(PILOT, max(400, n // 5))
    for i, pilot in enumerate((base, base, 2 * base, 2 * base)):
        m, t, _ = _games(lg, pilot, random.Random(_seed(f"{seed}-p{i}")), kh, ka)
        h = sum((x + y) / 2 for x, y in zip(t, m)) / pilot
        a = sum((x - y) / 2 for x, y in zip(t, m)) / pilot
        kh *= (home_pts / max(h, 1.0)) ** 0.4  # points grow ~k^2.5, so step gently
        ka *= (away_pts / max(a, 1.0)) ** 0.4
    parts = parallel.chunks(_chunk, n, (league, seed, kh, ka))
    return {"margins": [x for p in parts for x in p[0]], "totals": [x for p in parts for x in p[1]],
            "ot": [x for p in parts for x in p[2]]}


def _chunk(league, seed, kh, ka, n, i):
    return _games(LEAGUES[league], n, random.Random(_seed(f"{seed}-{i}") if i else _seed(seed)), kh, ka)


def run_at(league, margin, total, n=20000, seed=0):
    return run(league, (total + margin) / 2, (total - margin) / 2, n=n, seed=seed)


# --- pricing (same outputs as drivesim.price_spread / price_total) ------------
def price_spread(league, model_margin, model_total, home_spread, market_total, anchor,
                 home_odds=None, away_odds=None, n=20000, seed=0):
    """Side, fair line, cover chance (pushes excluded), push chance, EV and tier from games played
    out snap by snap at the market-anchored margin. The anchor starts at the margin where the line
    is a coin flip (key numbers move it off -spread) and moves part way toward the model."""
    from . import anchor as anc
    from . import drivesim
    t = market_total or model_total or 45.0
    fair_m = anc.fair(model_margin, drivesim.market_mean(league, home_spread, t), *anchor)
    hc, push, ac = drivesim.cover(run_at(league, fair_m, t, n=n, seed=f"{seed}-s"), home_spread)
    side = "home" if model_margin + home_spread >= 0 else "away"
    p_cover = hc if side == "home" else ac
    p_lose = 1 - p_cover - push
    ev = drivesim._ev(p_cover, p_lose, home_odds if side == "home" else away_odds)
    return {"side": side, "fair_line": round(-fair_m, 1),
            "cover_prob": round(p_cover / (p_cover + p_lose), 3) if p_cover + p_lose else None,
            "push_prob": round(push, 3), "ev": round(ev, 3), "tier": anc.tier(ev)}


def price_total(league, model_margin, model_total, line, anchor, over_odds=None, under_odds=None, n=20000, seed=0):
    from . import anchor as anc
    from . import drivesim
    if model_total is None or line is None:
        return None
    side = "over" if model_total >= line else "under"
    fair_t = anc.fair(model_total, line, *anchor)
    over, push, under = drivesim.over_under(run_at(league, model_margin, fair_t, n=n, seed=f"{seed}-t"), line)
    p, q = (over, under) if side == "over" else (under, over)
    odds = over_odds if side == "over" else under_odds
    ev = drivesim._ev(p, q, odds)
    t = anc.tier(ev)
    return {"side": side, "line": line, "model": round(model_total, 1), "edge": round(abs(model_total - line), 1),
            "prob": round(p / (p + q), 3) if p + q else None, "push_prob": round(push, 3), "odds": odds,
            "fair": round(fair_t, 1), "ev": round(ev, 4), "tier": t, "play": t in ("play", "strong")}
