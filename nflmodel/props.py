"""NFL anytime-TD probabilities.

Each active skill player's expected TDs per game come from his usage:
carries and targets by field zone (goal line <= 5, red zone 6-20, elsewhere),
each worth the league's TD rate for that opportunity, blended 75/25 with his
actual TDs, weighted toward recent games, shrunk toward a depth-chart prior,
then scaled by tonight's projected team points vs the team's usual output.
Active players = ESPN depth chart (QB1, RB1-3, WR1-4, TE1-2, FB1) minus
anyone listed Out / Doubtful / IR.
"""
from sportscommon import props as sprops

from . import starters

ZONES = ("car_gl", "car_rz", "car_o", "tgt_gl", "tgt_rz", "tgt_o")
# expected TDs per game by depth-chart slot (prior for thin samples / new players)
SLOT_PRIOR = {("qb", 1): 0.12, ("rb", 1): 0.42, ("rb", 2): 0.18, ("rb", 3): 0.07, ("wr", 1): 0.34, ("wr", 2): 0.24,
              ("wr", 3): 0.14, ("wr", 4): 0.07, ("te", 1): 0.20, ("te", 2): 0.08, ("fb", 1): 0.04}
PRIOR_GAMES = 3.0
DECAY = 0.85  # per week of age within the season
LAST_SEASON_WEIGHT = 0.3  # per game
MAX_SLOTS = {"qb": 1, "rb": 3, "wr": 4, "te": 2, "fb": 1}


def league_rates(player_games):
    tot = {z: [0, 0] for z in ZONES}
    for r in player_games:
        for z, (n, td) in r["o"].items():
            if z in tot:
                tot[z][0] += n
                tot[z][1] += td
    return {z: (td / n if n else 0.0) for z, (n, td) in tot.items()}


def player_rates(cur_games, prev_games, rates, week):
    """{pid: {'name','team','rate','games'}}: expected TDs per game (usage-based, recency-weighted)."""
    acc = {}
    for rows, season_w in ((cur_games, None), (prev_games, LAST_SEASON_WEIGHT)):
        for r in rows:
            if season_w is None and r["week"] >= week:
                continue
            w = season_w if season_w is not None else DECAY ** max(0, week - 1 - r["week"])
            xtd = sum(n * rates.get(z, 0) for z, (n, _) in r["o"].items())
            td = sum(t for _, t in r["o"].values())
            a = acc.setdefault(r["pid"], {"name": r["name"], "team": r["team"], "w": 0.0, "v": 0.0, "games": 0, "last_week": 0})
            a["w"] += w
            a["v"] += w * (0.75 * xtd + 0.25 * td)
            a["games"] += 1
            if season_w is None:
                a["team"] = r["team"]  # current team wins
                a["last_week"] = max(a["last_week"], r["week"])
    return acc


def candidates(team, espn, acc, week):
    """Active skill players for `team`: [(pid or None, name, pos, rank)]."""
    if espn and espn.get("skill", {}).get(team):
        out, used = [], {}
        for pos, rank, name in espn["skill"][team]:
            if rank > MAX_SLOTS.get(pos, 0):
                continue
            if starters.unavailable(espn["status"].get((team, starters.norm_name(name)), "")):
                continue
            k = sprops.short_key(name)
            pid = next((p for p, a in acc.items() if a["team"] == team and sprops.short_key(a["name"]) == k), None)
            if pid in used:
                continue
            used[pid] = True
            out.append((pid, name, pos, rank))
        return out
    # no depth chart: players with usage in the team's recent games
    recent = [(p, a) for p, a in acc.items() if a["team"] == team and a["last_week"] >= week - 3 and a["games"] >= 1]
    recent.sort(key=lambda x: -(x[1]["v"] / x[1]["w"] if x[1]["w"] else 0))
    return [(p, a["name"], "flex", 1) for p, a in recent[:8]]


def team_avg_points(games, season, week, prior_games=3.0):
    """{team: points per game this season before `week`, shrunk toward the league average}."""
    done = [g for g in games if g["season"] == season and g["completed"] and g["week"] < week]
    lg = sum(g["home_pts"] + g["away_pts"] for g in done) / (2 * len(done)) if done else 22.0
    acc = {}
    for g in done:
        for t, pts in ((g["home"], g["home_pts"]), (g["away"], g["away_pts"])):
            a = acc.setdefault(t, [0.0, 0])
            a[0] += pts
            a[1] += 1
    return {t: (s_ + prior_games * lg) / (n + prior_games) for t, (s_, n) in acc.items()}, lg


def team_players(team, team_points, espn, acc, team_avg, week):
    """[{'pid','name','team','pos','lam'}] for one side of a game.

    lam = the player's usual expected TDs per game x (tonight's projected team
    points / the team's usual points), so a strong scoring projection lifts
    everyone and a missing teammate doesn't inflate the others.
    """
    cands = candidates(team, espn, acc, week)
    scale = team_points / team_avg if team_avg else 1.0
    out = []
    for pid, name, pos, rank in cands:
        prior = SLOT_PRIOR.get((pos, rank), 0.08)
        a = acc.get(pid) if pid else None
        rate = ((a["v"] if a else 0.0) + PRIOR_GAMES * prior) / ((a["w"] if a else 0.0) + PRIOR_GAMES)
        out.append({"pid": pid, "name": name, "team": team, "pos": pos.upper() if pos != "flex" else None,
                    "lam": round(rate * scale, 4), "games": a["games"] if a else 0})
    return out
