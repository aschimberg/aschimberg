"""Synthetic ESPN NBA API for tests: scoreboards, summaries (box + pickcenter), injuries."""
import random
from datetime import date, datetime, timedelta, timezone

from nbamodel import config

TEAMS = sorted(config.TEAMS)
STRENGTH = {t: (i - 14.5) * 0.6 for i, t in enumerate(TEAMS)}  # net points vs average
NAMES = {t: f"{t} City {t.title()}s" for t in TEAMS}


def _players(t):
    # 9-man rotation; player 0 is the star
    return [(str(1000 + TEAMS.index(t) * 10 + k), f"{t} Player{k}", 36 - 3 * k if k < 8 else 10) for k in range(9)]


class FakeESPN:
    def __init__(self, today=date(2026, 10, 22), seed=7, injured=None):
        self.rng = random.Random(seed)
        self.today = today
        self.injured = injured or {}  # {team: [(pid, name, status)]}
        self.games = {}  # id -> event-ish
        self.calls = []
        self._make_season(2026, date(2025, 10, 21), 60)
        self._make_season(2027, date(2026, 10, 20), (today - date(2026, 10, 20)).days + 3)

    def _make_season(self, season, start, n_days):
        gid = season * 100000
        for d in range(n_days):
            day = start + timedelta(days=d)
            order = TEAMS[:]
            self.rng.shuffle(order)
            for k in range(0, 10, 2):  # 5 games a day
                h, a = order[k], order[k + 1]
                gid += 1
                margin_exp = STRENGTH[h] - STRENGTH[a] + 2.5
                spread = -round(margin_exp * 2) / 2
                total = 224.5
                done = day < self.today
                hs = as_ = None
                if done:
                    m = round(margin_exp + self.rng.gauss(0, 12))
                    if m == 0:
                        m = 1
                    tot = round(225 + self.rng.gauss(0, 15))
                    hs, as_ = (tot + m) // 2, (tot - m) // 2
                    if hs == as_:
                        hs += 1
                start_dt = datetime(day.year, day.month, day.day, 23, 30, tzinfo=timezone.utc)
                self.games[str(gid)] = {"id": str(gid), "season": season, "day": day, "start": start_dt, "home": h, "away": a,
                                        "hs": hs, "as": as_, "done": done, "spread": spread, "total": total}

    def _event(self, g):
        state = "post" if g["done"] else "pre"
        odds = None
        if not g["done"]:
            sp = g["spread"]
            odds = [{"provider": {"name": "Draft Kings", "displayName": "Draft Kings"},
                     "details": f"{g['home'] if sp < 0 else g['away']} {-abs(sp):g}", "overUnder": g["total"], "spread": sp,
                     "moneyline": {"home": {"close": {"odds": "-150"}, "open": {"odds": "-140"}},
                                   "away": {"close": {"odds": "+130"}, "open": {"odds": "+120"}}},
                     "pointSpread": {"home": {"close": {"line": f"{sp:+g}", "odds": "-110"}, "open": {"line": f"{sp + 0.5:+g}", "odds": "-110"}},
                                     "away": {"close": {"line": f"{-sp:+g}", "odds": "-110"}}},
                     "total": {"over": {"close": {"line": f"o{g['total']}", "odds": "-110"}, "open": {"line": f"o{g['total'] - 1}"}},
                               "under": {"close": {"line": f"u{g['total']}", "odds": "-110"}}}}]

        def comp(side):
            t = g["home"] if side == "home" else g["away"]
            sc = g["hs"] if side == "home" else g["as"]
            return {"homeAway": side, "score": str(sc if sc is not None else 0),
                    "team": {"id": str(TEAMS.index(t) + 1), "abbreviation": t, "displayName": NAMES[t], "shortDisplayName": t.title(),
                             "logo": f"https://example.test/{t}.png"},
                    "records": [{"type": "total", "summary": "1-0"}]}
        return {"id": g["id"], "date": g["start"].strftime("%Y-%m-%dT%H:%MZ"),
                "season": {"year": g["season"], "type": 2},
                "competitions": [{"neutralSite": False, "competitors": [comp("home"), comp("away")],
                                  "status": {"type": {"state": state, "completed": g["done"], "shortDetail": "Final" if g["done"] else "7:30 PM"}},
                                  "odds": odds, "venue": {"fullName": "Arena"}, "broadcasts": []}]}

    def _summary(self, g):
        box_teams, box_players = [], []
        for side, t, pts in (("home", g["home"], g["hs"]), ("away", g["away"], g["as"])):
            fga = 88
            fta = 22
            oreb = 10
            tov = 13
            box_teams.append({"team": {"abbreviation": t}, "homeAway": side, "statistics": [
                {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": f"{(pts - 18) // 2}-{fga}"},
                {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": f"17-{fta}"},
                {"name": "offensiveRebounds", "displayValue": str(oreb)},
                {"name": "totalTurnovers", "displayValue": str(tov)}]})
            names = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK", "OREB", "DREB", "PF", "+/-"]
            ath = []
            for pid, nm, mins in _players(t):
                out = any(pid == x[0] for x in self.injured.get(t, [])) and g["season"] == 2027 and g["day"] >= self.today - timedelta(days=1)
                if out:
                    ath.append({"athlete": {"id": pid, "displayName": nm}, "didNotPlay": True, "stats": []})
                    continue
                star = pid.endswith("0") and int(pid) % 10 == 0
                p = 28 if star else 10
                ath.append({"athlete": {"id": pid, "displayName": nm}, "starter": mins >= 30, "didNotPlay": False,
                            "stats": [str(mins), str(p), f"{p // 2}-{p // 2 + 8}", "1-3", "2-2", "6", "5" if star else "2",
                                      "2", "1", "0", "1", "5", "2", "+3"]})
            box_players.append({"team": {"abbreviation": t}, "statistics": [{"names": names, "athletes": ath}]})
        return {"boxscore": {"teams": box_teams, "players": box_players},
                "pickcenter": [{"provider": {"name": "Draft Kings"}, "spread": g["spread"], "overUnder": g["total"],
                                "overOdds": -110, "underOdds": -110,
                                "homeTeamOdds": {"moneyLine": -150, "spreadOdds": -110}, "awayTeamOdds": {"moneyLine": 130, "spreadOdds": -110}}]}

    def __call__(self, url):
        self.calls.append(url)
        if "scoreboard?dates=" in url:
            ds = url.split("dates=")[1][:8]
            day = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
            season = config.season_for(day)
            cal = sorted({g["day"] for g in self.games.values() if g["season"] == season})
            evs = [self._event(g) for g in self.games.values() if g["day"] == day]
            return {"leagues": [{"calendar": [f"{d.isoformat()}T07:00Z" for d in cal]}], "events": evs}
        if "summary?event=" in url:
            return self._summary(self.games[url.split("event=")[1]])
        if url.endswith("/injuries"):
            return {"injuries": [{"displayName": NAMES[t], "injuries": [
                {"status": st, "athlete": {"displayName": nm, "links": [{"href": f"https://www.espn.com/nba/player/_/id/{pid}/x"}]}}
                for pid, nm, st in rows]} for t, rows in self.injured.items()]}
        raise KeyError(url)
