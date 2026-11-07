"""Situational flags on the college football slate (poll rankings, not power ratings).

- lower_ranked_home_fav: both teams ranked, the home team ranked lower, favored by 7 or less.
- unranked_home_fav: an unranked home team favored over a ranked team.
- ranked_night_1h_under: two ranked teams kicking off at 7 PM ET or later (first-half under).

Neutral-site games get no home flags. Records track how the home favorite does against the spread
for the first two, and first-half points against half the closing game total for the third (our
feeds carry no first-half lines).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
NIGHT_HOUR_ET = 19
LABELS = {
    "lower_ranked_home_fav": "Lower-ranked home fav ≤7",
    "unranked_home_fav": "Unranked home fav vs ranked",
    "ranked_night_1h_under": "Ranked night game · 1H under",
}


def compute(home_rank, away_rank, home_spread, neutral, start, start_tbd=False):
    out = []
    if not neutral and home_spread is not None and home_spread < 0:
        if home_rank and away_rank and home_rank > away_rank and -home_spread <= 7:
            out.append("lower_ranked_home_fav")
        if not home_rank and away_rank:
            out.append("unranked_home_fav")
    if home_rank and away_rank and start and not start_tbd:
        kick = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(ET)
        if kick.hour >= NIGHT_HOUR_ET:
            out.append("ranked_night_1h_under")
    return out


def half_line(total):
    """Stand-in first-half line: half the closing total, to the nearest half point."""
    return round(total) / 2 if total is not None else None


def grade(flags, home_spread, total, hp, ap, h1_pts):
    """Results for a finished game: home-favorite ATS and first-half under vs half the total."""
    res = {}
    if home_spread is not None and ({"lower_ranked_home_fav", "unranked_home_fav"} & set(flags)):
        m = hp - ap + home_spread
        res["home_fav_ats"] = "W" if m > 0 else "L" if m < 0 else "P"
    if "ranked_night_1h_under" in flags and h1_pts is not None and total is not None:
        d = h1_pts - half_line(total)
        res["h1_under"] = "W" if d < 0 else "L" if d > 0 else "P"
        res["h1_pts"] = h1_pts
    return res
