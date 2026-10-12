"""NFL model settings (env-overridable like the CFB model)."""
import os
from pathlib import Path


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "nfl"
CACHE_DIR = DATA_DIR / "cache"
SITE_DATA_DIR = ROOT / "site" / "nfl" / "data"

GAMES_URL = os.environ.get("NFL_GAMES_URL", "https://github.com/nflverse/nfldata/raw/master/data/games.csv")
PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz"

# Blend of independent margin sources (tuned weekly).
WEIGHTS = {"epa": _f("NFL_W_EPA", 0.6), "elo": _f("NFL_W_ELO", 0.4)}
HOME_FIELD = _f("NFL_HFA", 1.6)  # points; league HFA has shrunk to ~1.5
PLAYS_PER_GAME = _f("NFL_PLAYS", 62)  # converts EPA/play to points
EPA_DECAY = _f("NFL_EPA_DECAY", 0.93)  # weight per week of age
EPA_RIDGE = _f("NFL_EPA_RIDGE", 350)  # plays of evidence behind the prior
PRIOR_KEEP = _f("NFL_PRIOR_KEEP", 0.5)  # share of last season's rating kept as prior
GARBAGE_WP = (0.05, 0.95)  # drop plays outside this offense win-prob band

# Elo (538-style)
ELO_K = _f("NFL_ELO_K", 20)
ELO_REVERT = _f("NFL_ELO_REVERT", 1 / 3)
ELO_PER_POINT = 25.0
ELO_START_SEASON = 2009

# QB adjustment
QB_PRIOR_DB = _f("NFL_QB_PRIOR_DB", 250)  # dropbacks of shrinkage toward replacement level
QB_REPLACEMENT = _f("NFL_QB_REPLACEMENT", -0.08)  # EPA/dropback vs league average
QB_DB_PER_GAME = _f("NFL_QB_DB", 35)
QB_MAX_ADJ = _f("NFL_QB_MAX", 9)
QB_SEASON_WEIGHTS = (1.0, 0.7, 0.4)  # current, last, two seasons ago

REST_PER_DAY = _f("NFL_REST_PER_DAY", 0.05)  # post-2011 rest effects are tiny
MARGIN_SD = _f("NFL_MARGIN_SD", 13.5)

LEAN_EDGE = _f("NFL_LEAN_EDGE", 1.5)
PLAY_EDGE = _f("NFL_PLAY_EDGE", 3.0)
STRONG_EDGE = _f("NFL_STRONG_EDGE", 4.5)
CONF_HIGH, CONF_MEDIUM = 70, 55

TREND_SEASONS = int(_f("NFL_TREND_SEASONS", 5))
TUNING = os.environ.get("NFL_TUNING", "on") != "off"
MIN_TUNE_GAMES = int(_f("NFL_MIN_TUNE_GAMES", 25))
TUNE_PRIOR_GAMES = _f("NFL_TUNE_PRIOR_GAMES", 250)
SITE_URL = os.environ.get("SITE_URL", "")
