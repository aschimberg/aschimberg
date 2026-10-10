"""Deterministic SYNTHETIC stand-in for the CFBD API (tests / local preview only).

Responses mimic the CFBD v2 camelCase shapes. None of these teams' numbers are
real data.
"""
import random
from datetime import datetime, timedelta, timezone

CONFS = {
    "Big Twelve": ["Texas Tech", "Kansas State", "Baylor", "TCU", "Utah", "Iowa State", "Arizona", "BYU", "Cincinnati", "Houston"],
    "SEC": ["Texas", "Oklahoma", "Georgia", "Alabama", "LSU", "Ole Miss", "Tennessee", "Florida", "Auburn", "Missouri"],
}
FCS = ["Abilene Christian"]


def _saturday_of_week(year, week):
    # Week 1 Saturday = last Saturday of August.
    d = datetime(year, 8, 31, tzinfo=timezone.utc)
    while d.weekday() != 5:
        d -= timedelta(days=1)
    return d + timedelta(weeks=week - 1)


class FakeCFBD:
    def __init__(self, current_season=2026, completed_weeks=6, weeks=12, seed=7):
        self.current = current_season
        self.completed_weeks = completed_weeks
        self.weeks = weeks
        self.calls = 0
        rng = random.Random(seed)
        self.teams = [t for ts in CONFS.values() for t in ts]
        self.conf_of = {t: c for c, ts in CONFS.items() for t in ts}
        self.strength = {t: rng.gauss(0, 12) for t in self.teams}
        self.strength.update({t: -25 for t in FCS})
        self._games = {}
        self._lines = {}
        gid = 400000000
        for year in range(current_season - 7, current_season + 1):
            games, lines = [], []
            for week in range(1, weeks + 1):
                pool = self.teams[:]
                rng.shuffle(pool)
                # Two teams get a bye each week; one plays an FCS team in week 1.
                if week > 1:
                    pool = pool[2:]
                pairs = [(pool[i], pool[i + 1]) for i in range(0, len(pool) - 1, 2)]
                if week == 1:
                    pairs = pairs[:-1] + [(pairs[-1][0], FCS[0])]
                sat = _saturday_of_week(year, week)
                for i, (h, a) in enumerate(pairs):
                    gid += 1
                    start = sat + timedelta(hours=16 + (i % 4) * 3, minutes=30)
                    neutral = rng.random() < 0.05
                    hfa = 0 if neutral else 2.5
                    true_margin = self.strength[h] - self.strength[a] + hfa
                    completed = year < current_season or week <= completed_weeks
                    hp = ap = None
                    if completed:
                        m = round(true_margin + rng.gauss(0, 14))
                        if m == 0:
                            m = 3
                        base = rng.randint(17, 31)
                        hp, ap = (base + m, base) if m > 0 else (base, base - m)
                    games.append({
                        "id": gid, "season": year, "week": week, "seasonType": "regular",
                        "startDate": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "startTimeTBD": False,
                        "completed": completed, "neutralSite": neutral,
                        "conferenceGame": self.conf_of.get(h) == self.conf_of.get(a),
                        "venue": f"{h} Stadium", "homeTeam": h, "awayTeam": a,
                        "homeConference": self.conf_of.get(h, "UAC"), "awayConference": self.conf_of.get(a, "UAC"),
                        "homeClassification": "fbs" if h in self.conf_of else "fcs",
                        "awayClassification": "fbs" if a in self.conf_of else "fcs",
                        "homePoints": hp, "awayPoints": ap,
                    })
                    if a in FCS:
                        continue
                    close = round((-(true_margin + rng.gauss(0, 3))) * 2) / 2
                    opn = close + rng.choice([0, 0, 0.5, -0.5, 1, -1.5, 2.5])
                    total = round(rng.uniform(44, 64) * 2) / 2
                    lines.append({
                        "id": gid, "season": year, "week": week, "seasonType": "regular",
                        "homeTeam": h, "awayTeam": a,
                        "lines": [
                            {"provider": "DraftKings", "spread": close, "spreadOpen": opn, "overUnder": total,
                             "overUnderOpen": total - 1, "homeMoneyline": None, "awayMoneyline": None},
                            {"provider": "Bovada", "spread": close + 0.5, "spreadOpen": None, "overUnder": total,
                             "overUnderOpen": None},
                        ],
                    })
            self._games[year] = games
            self._lines[year] = lines

    # API surface ------------------------------------------------------------
    def _tick(self):
        self.calls += 1

    def calendar(self, year):
        self._tick()
        out = []
        for w in range(1, self.weeks + 1):
            sat = _saturday_of_week(year, w)
            out.append({"season": year, "week": w, "seasonType": "regular",
                        "startDate": (sat - timedelta(days=6)).strftime("%Y-%m-%dT00:00:00.000Z"),
                        "endDate": (sat + timedelta(days=1, hours=9)).strftime("%Y-%m-%dT%H:%M:%S.000Z")})
        return out

    def games(self, year, season_type="both", week=None):
        self._tick()
        return [g for g in self._games.get(year, []) if week is None or g["week"] == week]

    def lines(self, year, season_type="regular", week=None):
        self._tick()
        if season_type != "regular":
            return []
        return [l for l in self._lines.get(year, []) if week is None or l["week"] == week]

    def _noisy(self, year, sd, scale, offset):
        rng = random.Random(year * 13 + int(scale))
        return {t: offset + (self.strength[t] + rng.gauss(0, sd)) * scale for t in self.teams}

    def sp(self, year):
        self._tick()
        vals = self._noisy(year, 3, 1, 0)
        return [{"year": year, "team": t, "conference": self.conf_of[t], "rating": round(v, 1),
                 "offense": {"rating": round(30 + v / 2, 1)}, "defense": {"rating": round(26 - v / 2, 1)}}
                for t, v in vals.items()]

    def fpi(self, year):
        self._tick()
        return [{"year": year, "team": t, "conference": self.conf_of[t], "fpi": round(v, 1)}
                for t, v in self._noisy(year, 4, 0.9, 0).items()]

    def elo(self, year, week=None, season_type=None):
        self._tick()
        return [{"year": year, "team": t, "conference": self.conf_of[t], "elo": round(v)}
                for t, v in self._noisy(year, 4, 22, 1500).items()]

    def fbs_teams(self, year):
        self._tick()
        return [{"school": t, "conference": self.conf_of[t], "logos": None} for t in self.teams]

    def rankings(self, year, week=None, season_type=None):
        self._tick()
        top = sorted(self.teams, key=lambda t: -self.strength[t])[:12]
        return [{"season": year, "seasonType": "regular", "week": w, "polls": [
            {"poll": "Coaches Poll", "ranks": []},
            {"poll": "AP Top 25", "ranks": [{"rank": i + 1, "school": t} for i, t in enumerate(top)]}]}
            for w in (1, self.completed_weeks + 1)]

    def media(self, year, week=None, season_type="regular"):
        self._tick()
        return [{"id": g["id"], "outlet": "ESPN" if g["id"] % 2 else "FOX"}
                for g in self._games.get(year, []) if g["week"] == week]
