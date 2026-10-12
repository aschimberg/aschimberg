"""NHL model settings."""
import os
from pathlib import Path


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "nhl"
CACHE_DIR = DATA_DIR / "cache"
SITE_DATA_DIR = ROOT / "site" / "nhl" / "data"
API = os.environ.get("NHL_API", "https://api-web.nhle.com/v1")
REQUEST_PAUSE = _f("NHL_REQUEST_PAUSE", 0.15)  # be polite to the public API

# Margin sources (goals), tuned weekly
WEIGHTS = {"xg": _f("NHL_W_XG", 0.55), "goals": _f("NHL_W_GOALS", 0.20), "elo": _f("NHL_W_ELO", 0.25)}
HOME_ICE = _f("NHL_HOME_ICE", 0.20)  # goals of margin (~54% home win rate)
B2B_PENALTY = _f("NHL_B2B", 0.25)  # goals of margin for the 2nd night of a back-to-back (~5% win prob)
DECAY_PER_DAY = _f("NHL_DECAY", 0.985)  # ~46-day half-life
RIDGE_GAMES = _f("NHL_RIDGE", 10)  # games of evidence behind the prior
PRIOR_KEEP = _f("NHL_PRIOR_KEEP", 0.6)

# Goalies
GOALIE_PRIOR_SHOTS = _f("NHL_GOALIE_PRIOR", 1500)  # unblocked shots of shrinkage toward 0 GSAx
GOALIE_LAST_SEASON_WEIGHT = 0.6

# Elo
ELO_K = _f("NHL_ELO_K", 8)
ELO_REVERT = 0.3
ELO_PER_GOAL = _f("NHL_ELO_PER_GOAL", 150)

# Score distribution
EN_GOAL_LEAD1 = _f("NHL_EN1", 0.22)  # P(empty-net goal | leading by 1 late)
EN_GOAL_LEAD2 = _f("NHL_EN2", 0.10)
OT_DECIDED = 0.6  # share of OTs decided in 3-on-3 (rest go to a shootout)
MAX_GOALS = 12

# Picks (expected value per unit)
LEAN_EV = _f("NHL_LEAN_EV", 0.025)
PLAY_EV = _f("NHL_PLAY_EV", 0.05)
STRONG_EV = _f("NHL_STRONG_EV", 0.08)
TOTAL_MIN_EV = _f("NHL_TOTAL_EV", 0.03)
CONF_HIGH, CONF_MEDIUM = 70, 55
SLATE_DAYS = 2  # today + tomorrow

TUNING = os.environ.get("NHL_TUNING", "on") != "off"
MIN_TUNE_GAMES = int(_f("NHL_MIN_TUNE_GAMES", 40))
TUNE_PRIOR_GAMES = _f("NHL_TUNE_PRIOR_GAMES", 400)
SITE_URL = os.environ.get("SITE_URL", "")


def season_id(d):
    """NHL season id (e.g. 20262027) for a date."""
    y = d.year if d.month >= 8 else d.year - 1
    return y * 10000 + y + 1
