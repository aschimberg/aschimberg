"""SYNTHETIC nflverse-shaped data for tests (not real results)."""
import csv
import io
import random
from datetime import date, timedelta

from nflmodel import data, teams

HEADER = ["game_id", "season", "game_type", "week", "gameday", "weekday", "gametime", "away_team", "away_score",
          "home_team", "home_score", "location", "result", "total", "overtime", "away_rest", "home_rest",
          "away_moneyline", "home_moneyline", "spread_line", "away_spread_odds", "home_spread_odds", "total_line",
          "under_odds", "over_odds", "div_game", "roof", "surface", "temp", "wind", "away_qb_id", "home_qb_id",
          "away_qb_name", "home_qb_name", "stadium"]


def build(current=2026, completed_weeks=5, seed=3):
    rng = random.Random(seed)
    abbrs = sorted(teams.TEAMS)
    strength = {t: rng.gauss(0, 6) for t in abbrs}
    qbs = {t: (f"00-{i:07d}", f"QB {t}") for i, t in enumerate(abbrs)}
    rows, pbp = [], {}
    for season in range(current - 20, current + 1):
        agg = {"team_games": [], "qb_games": [], "player_games": [], "td_scorers": {}}
        kick = date(season, 9, 10)
        for week in range(1, 19):
            order = abbrs[:]
            rng.shuffle(order)
            for i in range(0, 32, 2):
                h, a = order[i], order[i + 1]
                gid = f"{season}_{week:02d}_{a}_{h}"
                done = season < current or week <= completed_weeks
                mu = strength[h] - strength[a] + 1.6
                spread_line = round(mu * 2) / 2
                hs = as_ = ""
                if done:
                    m = round(rng.gauss(mu, 13))
                    base = rng.randint(13, 27)
                    hs, as_ = (base + m, base) if m >= 0 else (base, base - m)
                backup = season == current and week == completed_weeks + 1  # KC starts a backup
                rows.append({
                    "game_id": gid, "season": season, "game_type": "REG", "week": week,
                    "gameday": (kick + timedelta(weeks=week - 1)).isoformat(), "weekday": "Sunday", "gametime": "13:00",
                    "away_team": a, "away_score": as_, "home_team": h, "home_score": hs, "location": "Home",
                    "result": (hs - as_) if done else "", "total": (hs + as_) if done else "", "overtime": "0",
                    "away_rest": 7, "home_rest": 7, "away_moneyline": "", "home_moneyline": "",
                    "spread_line": spread_line if (done or week <= completed_weeks + 1) else "",
                    "away_spread_odds": -110, "home_spread_odds": -110, "total_line": 44.5, "under_odds": -110,
                    "over_odds": -110, "div_game": int(teams.division(h) == teams.division(a)), "roof": "outdoors",
                    "surface": "grass", "temp": "", "wind": "",
                    "away_qb_id": "00-9999999" if backup and a == "KC" else qbs[a][0],
                    "home_qb_id": "00-9999999" if backup and h == "KC" else qbs[h][0],
                    "away_qb_name": "Backup QB" if backup and a == "KC" else qbs[a][1],
                    "home_qb_name": "Backup QB" if backup and h == "KC" else qbs[h][1], "stadium": f"{h} Field",
                })
                if done and season >= current - 2:
                    for off, opp, home in ((h, a, 1), (a, h, 0)):
                        e = 0.01 * (strength[off] - strength[opp]) + rng.gauss(0, 0.08)
                        agg["team_games"].append({"game_id": gid, "season": season, "week": week, "season_type": "regular",
                                                  "team": off, "opp": opp, "home": home, "n": 60, "epa": round(60 * e, 3),
                                                  "succ": 27, "pass_n": 36, "pass_epa": round(36 * e, 3), "rush_n": 24,
                                                  "rush_epa": round(24 * e, 3)})
                        agg["qb_games"].append({"game_id": gid, "season": season, "week": week, "team": off,
                                                "qb": qbs[off][0], "name": qbs[off][1], "db": 36, "epa": round(36 * (e + 0.05), 3)})
                        # skill players: RB1 gets goal-line carries, WR1/TE1 targets; TDs ~ usage
                        for role, cells in (("RB", {"car_gl": 2, "car_rz": 3, "car_o": 14, "tgt_o": 3}),
                                            ("WR", {"tgt_rz": 2, "tgt_o": 7}), ("TE", {"tgt_gl": 1, "tgt_o": 4})):
                            pid = f"{off}-{role}1"
                            o = {z: [n, 1 if rng.random() < 0.08 * n else 0] for z, n in cells.items()}
                            agg["player_games"].append({"game_id": gid, "season": season, "week": week, "team": off,
                                                        "pid": pid, "name": f"{role[0]}.{off}{role}", "o": o})
                            if any(t for _, t in o.values()):
                                agg["td_scorers"].setdefault(gid, []).append([pid, f"{role[0]}.{off}{role}", off])
        pbp[season] = agg
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    games = data.parse_games(buf.getvalue())
    return games, (lambda s: pbp.get(s, {"team_games": [], "qb_games": []}))
