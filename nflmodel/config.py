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
DEFAULT_WEIGHTS, DEFAULT_HOME_FIELD = dict(WEIGHTS), HOME_FIELD  # untuned starting point (backtests)
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

ODDS_API_MAX_AGE = _f("NFL_ODDS_API_MAX_AGE", 6)  # hours between The Odds API refetches (credit budget)

# Over/under picks: model total vs market total (points)
TOTAL_SD = _f("NFL_TOTAL_SD", 13.5)  # sd of final combined score around a good projection
TOTAL_LEAN_EDGE = _f("NFL_TOTAL_LEAN", 2.0)
TOTAL_PLAY_EDGE = _f("NFL_TOTAL_PLAY", 3.5)
TOTAL_STRONG_EDGE = _f("NFL_TOTAL_STRONG", 5.0)

# Anytime-TD best bets
PROPS_PER_GAME = int(_f("NFL_PROPS_PER_GAME", 3))
PROPS_MIN_EV = _f("NFL_PROPS_MIN_EV", 0.03)
PROPS_MIN_PROB = _f("NFL_PROPS_MIN_PROB", 0.20)
PROPS_MODEL_WEIGHT = _f("NFL_PROPS_MODEL_WEIGHT", 0.4)  # model share of the log-odds when blended with the market
PROPS_MAX_RATIO = _f("NFL_PROPS_MAX_RATIO", 2.5)  # no bet when model and market probabilities differ by more than this
PROPS_MIN_GAMES = int(_f("NFL_PROPS_MIN_GAMES", 2))  # usage history needed before a player can be a best bet
PROPS_TD_PER_POINT = _f("NFL_PROPS_TD_PER_POINT", 0.112)  # offensive TDs per point scored (2018-25: ~2.5 TDs per 22 pts)
PROPS_HOURS = _f("NFL_PROPS_HOURS", 36)  # fetch prop odds only for games starting within this window

# Market anchoring (sportscommon/anchor.py): spreads and totals are priced at
# line + ANCHOR_WEIGHT x (model - line), moved at most ANCHOR_MAX_SHIFT points.
# Tiers come from EV at that price; the model's own projection is still shown.
ANCHOR_WEIGHT = _f("NFL_ANCHOR_WEIGHT", 0.15)
ANCHOR_MAX_SHIFT = _f("NFL_ANCHOR_MAX_SHIFT", 1.5)
ANCHOR = (ANCHOR_WEIGHT, ANCHOR_MAX_SHIFT)

# Game simulation (sportscommon/drivesim.py): games simulated per matchup for
# win chance, cover and over/under prices, and fewer per game in backtests
SIMS = int(_f("NFL_SIMS", 20000))
SIMS_BACKTEST = int(_f("NFL_SIMS_BACKTEST", 4000))
