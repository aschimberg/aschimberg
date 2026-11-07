"""Situational NFL angles for the briefing, with each angle's ATS record from nflverse history.

- dog_off_shutout: an underdog whose previous game (same season) was a loss in which it scored 0.
- dog_off_blowout: an underdog whose previous game (same season) was a loss by BLOWOUT+ points.
- home_dog_7: a home underdog of 7 or more (not at a neutral site).

Records cover every completed game with a closing line since 1999 (nflverse), graded from the
qualifying team's side. The current week's games use the live line.
"""
from collections import defaultdict

BLOWOUT = 21
LABELS = {
    "dog_off_shutout": "Underdogs off a shutout loss",
    "dog_off_blowout": f"Underdogs off a blowout loss ({BLOWOUT}+)",
    "home_dog_7": "Home dogs of 7 or more",
}


def _last_games(games):
    """{(season, team): [(start, scored, allowed), ...]} for completed games, in order."""
    out = defaultdict(list)
    for g in sorted((g for g in games if g.get("completed") and g.get("home_pts") is not None), key=lambda g: g["start"] or ""):
        out[(g["season"], g["home"])].append((g["start"], g["home_pts"], g["away_pts"]))
        out[(g["season"], g["away"])].append((g["start"], g["away_pts"], g["home_pts"]))
    return out


def _prev(last, season, team, start):
    prior = [x for x in last.get((season, team), []) if (x[0] or "") < (start or "")]
    return prior[-1] if prior else None


def hits(g, home_spread, last):
    """[(angle, side)] for one game given the home spread (negative = home favored)."""
    if home_spread is None or home_spread == 0:
        return []
    dog = "home" if home_spread > 0 else "away"
    out = []
    p = _prev(last, g["season"], g[dog], g["start"])
    if p:
        scored, allowed = p[1], p[2]
        if scored == 0 and allowed > 0:
            out.append(("dog_off_shutout", dog))
        if allowed - scored >= BLOWOUT:
            out.append(("dog_off_blowout", dog))
    if dog == "home" and home_spread >= 7 and not g.get("neutral"):
        out.append(("home_dog_7", "home"))
    return out


def records(games):
    """ATS record of every angle over completed games with a closing line."""
    last = _last_games(games)
    rec = {k: {"W": 0, "L": 0, "P": 0} for k in LABELS}
    for g in games:
        if not g.get("completed") or g.get("spread") is None or g.get("home_pts") is None:
            continue
        m = g["home_pts"] - g["away_pts"] + g["spread"]
        for key, side in hits(g, g["spread"], last):
            v = m if side == "home" else -m
            rec[key]["W" if v > 0 else "L" if v < 0 else "P"] += 1
    for r in rec.values():
        n = r["W"] + r["L"]
        r["pct"] = round(r["W"] / n, 3) if n else None
    return rec


def text(key, rec):
    r = rec[key]
    pct = f" ({r['pct'] * 100:.0f}%)" if r["pct"] is not None else ""
    return f"{LABELS[key]} have gone {r['W']}-{r['L']}-{r['P']} ATS{pct} since 1999"
