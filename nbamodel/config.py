"""NBA model settings."""
import os
from pathlib import Path


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "nba"
CACHE_DIR = DATA_DIR / "cache"
SITE_DATA_DIR = ROOT / "site" / "nba" / "data"
ESPN_API = os.environ.get("NBA_ESPN_API", "https://site.api.espn.com/apis/site/v2/sports/basketball/nba")
REQUEST_PAUSE = _f("NBA_REQUEST_PAUSE", 0.1)  # be polite to ESPN
# Box scores fetched per run for past seasons (the first run backfills a season in chunks)
BOX_BUDGET = int(_f("NBA_BOX_BUDGET", 700))

# Margin sources (points), refit weekly; defaults are the pre-backtest starting point
DEFAULT_WEIGHTS = {"eff": 0.45, "pts": 0.25, "elo": 0.30}
DEFAULT_HOME_COURT = 2.3
WEIGHTS = {"eff": _f("NBA_W_EFF", DEFAULT_WEIGHTS["eff"]), "pts": _f("NBA_W_PTS", DEFAULT_WEIGHTS["pts"]),
           "elo": _f("NBA_W_ELO", DEFAULT_WEIGHTS["elo"])}
HOME_COURT = _f("NBA_HOME_COURT", DEFAULT_HOME_COURT)  # points of margin
B2B_PENALTY = _f("NBA_B2B", 1.5)  # 2nd night of a back-to-back, points of margin
DECAY_PER_DAY = _f("NBA_DECAY", 0.99)  # ~70-day half-life
RIDGE_GAMES = _f("NBA_RIDGE", 10)  # games of evidence behind last season's (regressed) rating
PRIOR_KEEP = _f("NBA_PRIOR_KEEP", 0.55)  # share of last season's rating carried into this one
PACE_PRIOR_GAMES = _f("NBA_PACE_PRIOR", 10)

# Elo (538-style)
ELO_K = _f("NBA_ELO_K", 20)
ELO_REVERT = 0.25
ELO_PER_POINT = _f("NBA_ELO_PER_POINT", 28)

# Players: value of an absence, in points of margin
PLAYER_K = _f("NBA_PLAYER_K", 0.6)  # points of margin per Game Score point above replacement
REPLACEMENT_FRAC = _f("NBA_REPLACEMENT", 0.72)  # replacement Game Score/min as a share of league average
PLAYER_PRIOR_MIN = _f("NBA_PLAYER_PRIOR_MIN", 250)  # minutes of shrinkage toward replacement
PLAYER_MAX = _f("NBA_PLAYER_MAX", 9.0)
TEAM_ADJ_MAX = _f("NBA_TEAM_ADJ_MAX", 14.0)
ROTATION_MIN = 12  # expected minutes to count as a rotation player
AVAIL_GAMES = 10  # team games that define a player's recent availability
TOTAL_INJURY_SHARE = 0.5  # share of a team's injury adjustment that also comes off the total
STATUS_MISS = {"out": 1.0, "suspension": 1.0, "doubtful": 0.8, "questionable": 0.5, "day-to-day": 0.35,
               "game time decision": 0.5, "probable": 0.1}

# Score distribution
MARGIN_SD = _f("NBA_MARGIN_SD", 12.0)
TOTAL_SD = _f("NBA_TOTAL_SD", 17.0)

# Picks (points of edge vs the line)
LEAN_EDGE = _f("NBA_LEAN_EDGE", 1.5)
PLAY_EDGE = _f("NBA_PLAY_EDGE", 3.0)
STRONG_EDGE = _f("NBA_STRONG_EDGE", 5.0)
TOTAL_LEAN_EDGE = _f("NBA_TOTAL_LEAN", 3.0)
TOTAL_PLAY_EDGE = _f("NBA_TOTAL_PLAY", 5.0)
TOTAL_STRONG_EDGE = _f("NBA_TOTAL_STRONG", 8.0)
CONF_HIGH, CONF_MEDIUM = 70, 55
EARLY_GAMES = int(_f("NBA_EARLY_GAMES", 8))  # until both teams have played this many, picks cap at 'lean'
SLATE_DAYS = 2  # today + tomorrow (ET)

TUNING = os.environ.get("NBA_TUNING", "on") != "off"
MIN_TUNE_GAMES = int(_f("NBA_MIN_TUNE_GAMES", 60))
TUNE_PRIOR_GAMES = _f("NBA_TUNE_PRIOR_GAMES", 400)
ODDS_API_MAX_AGE = _f("NBA_ODDS_API_MAX_AGE", 6)
SITE_URL = os.environ.get("SITE_URL", "")

TEAMS = {
    "ATL": "East", "BOS": "East", "BKN": "East", "CHA": "East", "CHI": "East", "CLE": "East", "DET": "East",
    "IND": "East", "MIA": "East", "MIL": "East", "NY": "East", "ORL": "East", "PHI": "East", "TOR": "East",
    "WSH": "East", "DAL": "West", "DEN": "West", "GS": "West", "HOU": "West", "LAC": "West", "LAL": "West",
    "MEM": "West", "MIN": "West", "NO": "West", "OKC": "West", "PHX": "West", "POR": "West", "SAC": "West",
    "SA": "West", "UTAH": "West",
}


def season_for(d):
    """ESPN NBA season year for a date: the 2026-27 season is 2027."""
    return d.year + 1 if d.month >= 8 else d.year


def season_label(season):
    return f"{season - 1}–{str(season)[2:]}"

# Market anchoring (sportscommon/anchor.py): spreads and totals are priced at
# line + ANCHOR_WEIGHT x (model - line), moved at most ANCHOR_MAX_SHIFT points.
# Tiers come from EV at that price; the model's own projection is still shown.
ANCHOR_WEIGHT = _f("NBA_ANCHOR_WEIGHT", 0.15)
ANCHOR_MAX_SHIFT = _f("NBA_ANCHOR_MAX_SHIFT", 1.5)
ANCHOR = (ANCHOR_WEIGHT, ANCHOR_MAX_SHIFT)
