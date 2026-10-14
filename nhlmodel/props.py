"""NHL anytime-goal probabilities.

Each skater's goals per game at his team's usual scoring level come from
MoneyPuck: 70% individual expected goals + 30% actual goals per game, this
season weighted by games played and last season at half weight, shrunk
toward the forward/defense average by PRIOR_GAMES. Tonight's rate scales it
by the model's projected team goals vs the team's average this season.
Lineup = skaters dressed in the team's most recent game (NHL play-by-play).
"""
PRIOR_GAMES = 10.0
LAST_SEASON_WEIGHT = 0.5
IXG_SHARE = 0.7


def position_means(skaters):
    acc = {"F": [0.0, 0.0], "D": [0.0, 0.0]}
    for r in skaters.values():
        k = "D" if r.get("pos") == "D" else "F"
        acc[k][0] += IXG_SHARE * r["ixg"] + (1 - IXG_SHARE) * r["goals"]
        acc[k][1] += r["gp"]
    return {k: (v / n if n else (0.06 if k == "D" else 0.22)) for k, (v, n) in acc.items()}


def player_rate(pid, mp, means):
    cur, prev = mp.get("cur", {}).get(pid), mp.get("prev", {}).get(pid)
    pos = (cur or prev or {}).get("pos")
    prior = means["D" if pos == "D" else "F"]
    num = den = 0.0
    for r, w in ((cur, 1.0), (prev, LAST_SEASON_WEIGHT)):
        if r and r["gp"]:
            num += w * (IXG_SHARE * r["ixg"] + (1 - IXG_SHARE) * r["goals"])
            den += w * r["gp"]
    return (num + PRIOR_GAMES * prior) / (den + PRIOR_GAMES), pos, (cur or prev or {}).get("name")


def team_avg_goals(games, prior_games=10.0):
    """{team: regulation non-empty-net goals per game}, shrunk toward the league average."""
    acc, tot, n = {}, 0.0, 0
    for g in games:
        for side, opp in (("home", "away"), ("away", "home")):
            gl = g.get(f"reg_{side}")
            if gl is None:
                continue
            a = acc.setdefault(g[side], [0.0, 0])
            a[0] += gl
            a[1] += 1
            tot += gl
            n += 1
    lg = tot / n if n else 2.8
    return {t: (s + prior_games * lg) / (k + prior_games) for t, (s, k) in acc.items()}, lg


def team_players(team, lineup_game, proj_goals, team_avg, mp, means):
    """[{'pid','name','team','pos','lam'}] for skaters dressed in the team's last game."""
    if not lineup_game or not lineup_game.get("skaters"):
        return []
    side = "home" if lineup_game["home"] == team else "away"
    names = lineup_game.get("skater_names") or {}
    scale = proj_goals / team_avg if team_avg else 1.0
    out = []
    for pid in lineup_game["skaters"][side]:
        rate, pos, mp_name = player_rate(pid, mp, means)
        out.append({"pid": pid, "name": names.get(str(pid)) or mp_name or f"#{pid}", "team": team, "pos": pos,
                    "lam": round(rate * scale, 4)})
    return out
