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
WEIGHTS = {"xg": _f("NHL_W_XG", 0.35), "mp": _f("NHL_W_MP", 0.25), "goals": _f("NHL_W_GOALS", 0.15), "elo": _f("NHL_W_ELO", 0.25)}
MONEYPUCK = os.environ.get("NHL_MONEYPUCK", "on").lower() != "off"
GOALIE_MP_SHARE = _f("NHL_GOALIE_MP_SHARE", 0.5)  # weight on MoneyPuck's GSAx vs ours
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
# Measured on 1,312 regular-season 2025-26 games. Lambdas are regulation goals
# without empty-netters, so both tables are conditioned on that margin.
# Independent Poisson gives too few ties (0.17 vs 0.26: goals scored 6-on-5
# with the goalie pulled turn one-goal deficits into ties), so each regulation
# score is reweighted by its margin |m| = 0, 1, 2, 3, 4+.
MARGIN_WEIGHTS = (1.50, 1.07, 0.76, 0.80, 0.83)
# Chance the leader adds 0, 1, 2 empty-net goals, by regulation (non-EN) lead.
EN_GOALS = {1: (0.52, 0.39, 0.09), 2: (0.25, 0.72, 0.03), 3: (0.62, 0.38), 4: (0.97, 0.03)}
# win probability ≈ Φ(margin / WP_SCALE) under the sim; used to turn the market price into a margin
WP_SCALE = 2.58
OT_DECIDED = 0.6  # share of OTs decided in 3-on-3 (rest go to a shootout)
MAX_GOALS = 12

# Picks (expected value per unit)
LEAN_EV = _f("NHL_LEAN_EV", 0.025)
PLAY_EV = _f("NHL_PLAY_EV", 0.05)
STRONG_EV = _f("NHL_STRONG_EV", 0.08)
# Moneyline and puck-line chances are the model blended with the market's no-vig
# price (this share of the log-odds from the model) before EV is computed. Picking
# the side where the model most exceeds the market otherwise inflates its chance:
# underdog picks averaged 44% from the model, 41% from the market, 38% actual.
ML_MODEL_WEIGHT = _f("NHL_ML_MODEL_WEIGHT", 0.5)
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

ODDS_API_MAX_AGE = _f("NHL_ODDS_API_MAX_AGE", 6)  # hours between The Odds API refetches (credit budget)
