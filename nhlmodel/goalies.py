"""Goalie GSAx (goals saved above expected) and projected starters."""
from datetime import date, timedelta

from . import config


def gsax(seasons_games, xg_of):
    """seasons_games: [(weight, games list)]. Returns {goalie: {"rate", "shots"}}
    where rate = shrunk GSAx per unblocked shot faced."""
    acc = {}
    for w, games in seasons_games:
        for g in games:
            for s in g["shots"]:
                gid = s[12]
                if not gid or s[10]:
                    continue
                a = acc.setdefault(gid, [0.0, 0.0])
                a[0] += w * (xg_of(s) - s[11])
                a[1] += w
    n0 = config.GOALIE_PRIOR_SHOTS
    return {gid: {"rate": v / (n + n0), "shots": n} for gid, (v, n) in acc.items()}


def team_starts(games):
    """{team: [(date, goalie)] in date order} from cached games."""
    out = {}
    for g in sorted(games, key=lambda x: x.get("start") or ""):
        for side in ("home", "away"):
            gid = g.get(f"{side}_goalie")
            if gid:
                out.setdefault(g[side], []).append((g.get("date"), gid))
    return out


def projected_starter(team, game_date, starts, played_yesterday):
    """Most frequent starter over the last 10; the backup on a back-to-back
    if the usual starter played the night before."""
    hist = starts.get(team, [])[-10:]
    if not hist:
        return None, False
    counts = {}
    for _, gid in hist:
        counts[gid] = counts.get(gid, 0) + 1
    ranked = sorted(counts, key=lambda g: -counts[g])
    starter = ranked[0]
    if played_yesterday and hist[-1][1] == starter and len(ranked) > 1:
        return ranked[1], True
    return starter, False


def start_counts(starts):
    """{goalie: starts across all teams} over the cached seasons."""
    out = {}
    for hist in starts.values():
        for _, gid in hist:
            out[gid] = out.get(gid, 0) + 1
    return out


def is_backup(gid, usual, counts, share=0.6):
    """A confirmed starter who isn't the team's usual one counts as a backup only
    if he hasn't been a regular starter anywhere (a starter acquired by trade, or
    back from injury, isn't a backup)."""
    if gid is None or usual is None or gid == usual:
        return False
    return counts.get(gid, 0) < share * counts.get(usual, 0)


def baseline(team, starts, rates):
    """Start-weighted GSAx rate of the goalies a team has used recently."""
    hist = starts.get(team, [])[-20:]
    if not hist:
        return 0.0
    return sum(rates.get(g, {"rate": 0.0})["rate"] for _, g in hist) / len(hist)


def played_on(team, d, games_by_team_date):
    return (team, (date.fromisoformat(d) - timedelta(days=1)).isoformat()) in games_by_team_date
