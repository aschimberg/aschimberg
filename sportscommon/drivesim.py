"""Drive-by-drive Monte Carlo simulation for football (NFL and college).

Each simulated game:
1. Possessions: both teams get the same number of drives, drawn around the
   league average (pace is shared, which is what correlates the two scores).
2. Each drive ends in a touchdown, a field goal or nothing. A team's
   touchdown and field-goal chances are set so its expected points equal its
   projected points, with field goals a fixed share of scoring drives.
3. Touchdowns add the try: an extra point (or a miss), or a two-point try.
4. A tie goes to overtime: the winner (stronger team favored) adds a field
   goal or a touchdown; in the NFL a few overtime games still end tied.

Points come in 3s and 7s, so key margins (3, 7, 10, 14) appear on their
own, but independent drives miss some end-game behavior (late scores by the
trailing team), so a few near-key finishes are nudged onto the key (see
NFL_SNAP). Results are deterministic per seed.
"""
import math
import random
import zlib

# End-game nudges: a game finishing a point or two off a key margin is moved
# onto it (same side) with this chance, e.g. NFL 9 -> 7 half the time. Measured
# as the share of each margin that real games have fewer of than raw sims
# (NFL 2015-25, 3,106 games; FBS 2,856 games), routed to the nearest key
# number that real games have more of. The side never changes, so a bet's
# chance of covering moves only for lines between the two margins.
NFL_SNAP = {4: (3, 0.224), 1: (3, 0.075), 9: (7, 0.513), 15: (14, 0.238)}
CFB_SNAP = {4: (3, 0.087), 6: (7, 0.089), 9: (7, 0.215), 11: (10, 0.056), 15: (14, 0.066), 18: (17, 0.197),
            20: (21, 0.196), 22: (21, 0.021)}

LEAGUES = {
    # drives per team, field goals per touchdown drive, extra point %, 2-pt try %, 2-pt success %
    "nfl": {"default_total": 45.0, "drives": 11.0, "drive_sd": 0.6, "fg_ratio": 0.62, "xp": 0.95, "two": 0.07, "two_ok": 0.48,
            "ot_tie": 0.07, "ot_fg": 0.55,
            "snap": NFL_SNAP},
    "cfb": {"default_total": 55.0, "drives": 12.8, "drive_sd": 1.4, "fg_ratio": 0.42, "xp": 0.97, "two": 0.06, "two_ok": 0.45,
            "ot_tie": 0.0, "ot_fg": 0.35,
            "snap": CFB_SNAP},
}


def _seed(key):
    return zlib.crc32(str(key).encode()) & 0xFFFFFFFF


def _probs(points, drives, lg):
    """(p_td, p_fg) for a team expected to score `points` over `drives`."""
    td_val = lg["xp"] * 7 + (1 - lg["xp"]) * 6
    td_val = (1 - lg["two"]) * td_val + lg["two"] * (6 + 2 * lg["two_ok"])
    per_drive = max(points, 0.5) / drives
    p_td = per_drive / (td_val + 3 * lg["fg_ratio"])
    p_fg = lg["fg_ratio"] * p_td
    if p_td + p_fg > 0.92:  # extreme mismatches: cap and let touchdowns carry the rest
        p_td = min(0.85, per_drive / 7.0)
        p_fg = max(0.0, min(0.92 - p_td, (per_drive - p_td * td_val) / 3))
    return p_td, p_fg


def run(league, home_pts, away_pts, n=2500, seed=0):
    """Simulate n games. Returns {'margins', 'totals', 'ot'} (home minus away)."""
    lg = LEAGUES[league]
    rng = random.Random(_seed(seed))
    base = lg["drives"]
    hp = max(home_pts, 0.5)
    ap = max(away_pts, 0.5)
    p_home_ot = hp / (hp + ap)
    margins, totals, ots = [], [], []
    xp, two, two_ok = lg["xp"], lg["two"], lg["two_ok"]
    h_td, h_fg = _probs(hp, base, lg)
    a_td, a_fg = _probs(ap, base, lg)
    teams = ((h_td, h_fg), (a_td, a_fg))
    while len(margins) < n:
        d = max(6, round(rng.gauss(base, lg["drive_sd"])))
        score = [0, 0]
        for side, (p_td, p_fg) in enumerate(teams):
            s = 0
            for _ in range(d):
                u = rng.random()
                if u < p_td:
                    v = rng.random()
                    if v < two:
                        s += 8 if rng.random() < two_ok else 6
                    else:
                        s += 7 if rng.random() < xp else 6
                elif u < p_td + p_fg:
                    s += 3
            score[side] = s
        h, a = score
        ot = h == a
        if ot and rng.random() >= lg["ot_tie"]:
            pts = 3 if rng.random() < lg["ot_fg"] else (6 if league == "nfl" else 7)
            if rng.random() < p_home_ot:
                h += pts
            else:
                a += pts
        snap = lg.get("snap") or {}
        m = h - a
        if abs(m) in snap:
            k, q = snap[abs(m)]
            if rng.random() < q:  # the trailing team's late score lands it on the key number
                if m > 0:
                    a += m - k
                else:
                    h += -m - k
        margins.append(h - a)
        totals.append(h + a)
        ots.append(ot)
    return {"margins": margins, "totals": totals, "ot": ots}


def summary(res):
    ms = res["margins"]
    n = len(ms)
    return {"n": n, "margin": sum(ms) / n, "total": sum(res["totals"]) / n,
            "home_wp": (sum(1 for m in ms if m > 0) + 0.5 * sum(1 for m in ms if m == 0)) / n,
            "ot": sum(res["ot"]) / n}


def cover(res, home_spread):
    """(P home covers, P push, P away covers)."""
    n = len(res["margins"])
    hc = sum(1 for m in res["margins"] if m + home_spread > 0) / n
    push = sum(1 for m in res["margins"] if m + home_spread == 0) / n
    return hc, push, 1 - hc - push


def over_under(res, line):
    n = len(res["totals"])
    over = sum(1 for t in res["totals"] if t > line) / n
    push = sum(1 for t in res["totals"] if t == line) / n
    return over, push, 1 - over - push


def run_at(league, margin, total, n=2500, seed=0):
    """Simulate around a projected margin (home minus away) and total."""
    return run(league, (total + margin) / 2, (total - margin) / 2, n=n, seed=seed)


def _drive_counts(lg):
    """{drives: probability}: the rounded-normal drive count used in run()."""
    import math
    base, sd = lg["drives"], lg["drive_sd"]
    cdf = lambda x: 0.5 * (1 + math.erf((x - base) / (sd * math.sqrt(2))))
    out = {}
    for d in range(int(base - 5 * sd) - 1, int(base + 5 * sd) + 2):
        p = cdf(d + 0.5) - cdf(d - 0.5)
        if p > 0.005:  # drive counts rarer than 0.5% barely move a cover chance
            out[max(6, d)] = out.get(max(6, d), 0.0) + p
    tot = sum(out.values())
    return {d: p / tot for d, p in out.items()}


def _team_pmfs(points, lg, counts):
    """{drives: [P(team scores k)]} by exact convolution of drive outcomes."""
    p_td, p_fg = _probs(points, lg["drives"], lg)
    xp, two, ok = lg["xp"], lg["two"], lg["two_ok"]
    drive = {0: 1 - p_td - p_fg, 3: p_fg, 6: p_td * (two * (1 - ok) + (1 - two) * (1 - xp)),
             7: p_td * (1 - two) * xp, 8: p_td * two * ok}
    out, cur = {}, [1.0]
    for d in range(1, max(counts) + 1):
        nxt = [0.0] * (len(cur) + 8)
        for s, ps in enumerate(cur):
            if ps:
                for v, pv in drive.items():
                    nxt[s + v] += ps * pv
        cur = nxt
        if d in counts:
            out[d] = cur
    return out


def margin_pmf(league, margin, total):
    """Exact final-margin distribution of the simulation (every drive outcome
    weighted, overtime and margin weights applied): what run() converges to."""
    lg = LEAGUES[league]
    hp, ap = max((total + margin) / 2, 0.5), max((total - margin) / 2, 0.5)
    counts = _drive_counts(lg)
    hs, as_ = _team_pmfs(hp, lg, counts), _team_pmfs(ap, lg, counts)
    pm = {}
    for d, pd in counts.items():
        h = [(i, pd * x) for i, x in enumerate(hs[d]) if x > 1e-7]
        a = [(j, x) for j, x in enumerate(as_[d]) if x > 1e-7]
        for i, phi in h:
            for j, pa in a:
                pm[i - j] = pm.get(i - j, 0.0) + phi * pa
    tie = pm.pop(0, 0.0)
    p_home = hp / (hp + ap)
    td = 6 if league == "nfl" else 7
    if lg["ot_tie"]:
        pm[0] = tie * lg["ot_tie"]
    for pts, pp in ((3, lg["ot_fg"]), (td, 1 - lg["ot_fg"])):
        w = tie * (1 - lg["ot_tie"]) * pp
        pm[pts] = pm.get(pts, 0.0) + w * p_home
        pm[-pts] = pm.get(-pts, 0.0) + w * (1 - p_home)
    for am, (k, q) in (lg.get("snap") or {}).items():
        for sign in (1, -1):
            p = pm.get(sign * am, 0.0) * q
            if p:
                pm[sign * am] -= p
                pm[sign * k] = pm.get(sign * k, 0.0) + p
    z = sum(pm.values())
    return {m: p / z for m, p in pm.items()}


def market_mean(league, home_spread, total, _cache={}):
    """Projected margin at which the line is a coin flip for this simulation
    (key numbers move it off -spread), from the exact margin distribution."""
    t = round((total or LEAGUES[league]["default_total"]) / 3) * 3  # weak dependence on the total: cache by 3s
    key = (league, home_spread, t)
    if key in _cache:
        return _cache[key]

    def gap(m):
        pm = margin_pmf(league, m, t)
        hc = sum(p for x, p in pm.items() if x + home_spread > 0)
        ac = sum(p for x, p in pm.items() if x + home_spread < 0)
        return hc - ac

    lo, hi = -home_spread - 8, -home_spread + 8
    for _ in range(16):
        mid = (lo + hi) / 2
        if gap(mid) < 0:
            lo = mid
        else:
            hi = mid
    _cache[key] = (lo + hi) / 2
    return _cache[key]


# --- pricing ----------------------------------------------------------------
def _ev(p_win, p_lose, odds):
    from .odds import american_to_decimal
    return p_win * (american_to_decimal(odds or -110) - 1) - p_lose


def price_spread(league, model_margin, model_total, home_spread, market_total, anchor,
                 home_odds=None, away_odds=None, n=2500, seed=0):
    """Side, fair line, cover chance (pushes excluded), push chance, EV and tier
    from games simulated at the market-anchored margin.

    The anchor starts from the margin at which the line is a coin flip in the
    simulation (key numbers move it off -spread), and moves toward the model.
    """
    from . import anchor as anc
    t = market_total or model_total or LEAGUES[league].get("default_total", 45.0)
    mkt_m = market_mean(league, home_spread, t)
    fair_m = anc.fair(model_margin, mkt_m, *anchor)
    hc, push, ac = cover(run_at(league, fair_m, t, n=n, seed=seed), home_spread)
    side = "home" if model_margin + home_spread >= 0 else "away"
    p_cover = hc if side == "home" else ac
    p_lose = 1 - p_cover - push
    ev = _ev(p_cover, p_lose, home_odds if side == "home" else away_odds)
    return {"side": side, "fair_line": round(-fair_m, 1),
            "cover_prob": round(p_cover / (p_cover + p_lose), 3) if p_cover + p_lose else None,
            "push_prob": round(push, 3), "ev": round(ev, 3), "tier": anc.tier(ev)}


def price_total(league, model_margin, model_total, line, anchor, over_odds=None, under_odds=None, n=2500, seed=0):
    """Over/under from games simulated at the market-anchored total (same shape as totals.make_pick)."""
    from . import anchor as anc
    if model_total is None or line is None:
        return None
    side = "over" if model_total >= line else "under"
    fair_t = anc.fair(model_total, line, *anchor)
    over, push, under = over_under(run_at(league, model_margin, fair_t, n=n, seed=f"{seed}-t"), line)
    p, q = (over, under) if side == "over" else (under, over)
    odds = over_odds if side == "over" else under_odds
    ev = _ev(p, q, odds)
    t = anc.tier(ev)
    return {"side": side, "line": line, "model": round(model_total, 1), "edge": round(abs(model_total - line), 1),
            "prob": round(p / (p + q), 3) if p + q else None, "push_prob": round(push, 3), "odds": odds,
            "fair": round(fair_t, 1), "ev": round(ev, 4), "tier": t, "play": t in ("play", "strong")}
