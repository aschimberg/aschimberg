"""SYNTHETIC NHL data in the cached-game shape (tests only; not real results)."""
import random
from datetime import date, datetime, timedelta, timezone

TEAMS = ["BOS", "TOR", "MTL", "NYR", "PIT", "CAR", "FLA", "TBL", "EDM", "VAN", "COL", "DAL"]


def _game(rng, gid, d, h, a, strength, goalies, starter_idx):
    shots = []
    reg = {1: 0, 0: 0}
    for side, team, opp in ((1, h, a), (0, a, h)):
        g_def = goalies[opp][starter_idx[opp]]
        for _ in range(rng.randint(35, 50)):
            dist = rng.uniform(5, 65)
            ang = rng.uniform(0, 70)
            reb = int(rng.random() < 0.07)
            p = 1 / (1 + 2.718 ** -(-1.2 - 0.065 * dist + 1.1 * reb + 0.08 * (strength[team] - strength[opp])))
            goal = int(rng.random() < p)
            reg[side] += goal
            shots.append([side, rng.randint(1, 3), rng.randint(0, 1199), round(dist, 1), round(ang, 1),
                          rng.randrange(8), reb, int(rng.random() < 0.1), 0, 0, 0, goal, g_def])
    hs, as_ = reg[1], reg[0]
    last = "REG"
    if hs == as_:
        last = "OT"
        if rng.random() < 0.5:
            hs += 1
        else:
            as_ += 1
    start = datetime(d.year, d.month, d.day, 23, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": gid, "date": d.isoformat(), "start": start, "type": 2, "home": h, "away": a,
            "home_score": hs, "away_score": as_, "last_period": last, "reg_home": reg[1], "reg_away": reg[0],
            "en_home": 0, "en_away": 0, "home_goalie": goalies[h][starter_idx[h]], "away_goalie": goalies[a][starter_idx[a]],
            "goalies": {str(goalies[t][i]): f"G. {t}{i}" for t in (h, a) for i in (0, 1)}, "shots": shots}


def build(today=date(2026, 11, 10), seed=5):
    rng = random.Random(seed)
    strength = {t: rng.gauss(0, 1) for t in TEAMS}
    goalies = {t: (8470000 + i * 2, 8470001 + i * 2) for i, t in enumerate(TEAMS)}
    prev, cur = [], []
    gid = 2025020000
    for season_games, start, ndays in ((prev, date(2025, 10, 8), 150), (cur, date(2026, 10, 8), (today - date(2026, 10, 8)).days)):
        for k in range(ndays):
            d = start + timedelta(days=k)
            order = TEAMS[:]
            rng.shuffle(order)
            for i in range(0, 6, 2):
                gid += 1
                si = {t: (1 if rng.random() < 0.25 else 0) for t in TEAMS}
                season_games.append(_game(rng, gid, d, order[i], order[i + 1], strength, goalies, si))
    sched = {}
    for k in range(2):
        d = today + timedelta(days=k)
        order = TEAMS[:]
        rng.shuffle(order)
        for i in range(0, 8, 2):
            gid += 1
            h, a = order[i], order[i + 1]
            sched[gid] = {"id": gid, "type": 2, "start": f"{d.isoformat()}T23:30:00Z", "date": d.isoformat(),
                          "home": h, "away": a, "state": "FUT", "home_score": None, "away_score": None,
                          "venue": f"{h} Arena", "neutral": False}
    # yesterday's games make some teams play a back-to-back today
    for g in cur[-3:]:
        g["date"] = (today - timedelta(days=1)).isoformat()
    odds = {g["id"]: {"ml_home": -140, "ml_away": 120, "total": 6.5, "over_odds": -110, "under_odds": -110,
                      "pl_home": -1.5, "pl_home_odds": 170, "pl_away": 1.5, "pl_away_odds": -200, "book": "DraftKings"}
            for g in sched.values()}
    teams = {t: {"abbr": t, "name": f"{t} Club", "division": "Atlantic" if i < 6 else "Pacific",
                 "logo": None, "record": "5-3-1"} for i, t in enumerate(TEAMS)}
    return {"teams": teams, "season": 20262027, "prev": prev, "cur": cur, "sched": sched, "odds": odds}
