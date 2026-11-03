"""College basketball (D1 men) settings."""
import os
from pathlib import Path


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "cbb"
SITE = os.environ.get("CBB_ESPN_API", "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball")
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball"
THREADS = int(_f("CBB_THREADS", 8))
# Sportsbooks ESPN reports pre-game lines from (ESPN BET 58, DraftKings 100); 59 is live in-game odds.
PROVIDERS = ("100", "58", "40", "1002")

# Ratings: points per 100 possessions, additive opponent adjustment
HOME_COURT = _f("CBB_HOME_COURT", 3.0)       # points of margin at a true home game
PRIOR_GAMES = _f("CBB_PRIOR_GAMES", 5)        # games of evidence behind the preseason prior
REGRESS_EFF = _f("CBB_REGRESS_EFF", 0.6)      # share of last season's efficiency kept
REGRESS_TEMPO = _f("CBB_REGRESS_TEMPO", 0.8)
NEW_TEAM_GAP = _f("CBB_NEW_TEAM_GAP", 6.0)    # pts/100 below average for a D1 team with no history
NON_D1_OFF, NON_D1_DEF = -18.0, 14.0          # non-D1 opponents vs D1 average, pts/100
ITERATIONS = int(_f("CBB_ITER", 40))

# Score spread around a sharp projection (fit in the backtest); used for pricing
MARGIN_SD = _f("CBB_MARGIN_SD", 11.0)
TOTAL_SD = _f("CBB_TOTAL_SD", 15.5)

# Market anchoring (same rule as the other sports): fair = line + w x (model - line), capped
ANCHOR_WEIGHT = _f("CBB_ANCHOR_WEIGHT", 0.15)
ANCHOR_MAX_SHIFT = _f("CBB_ANCHOR_MAX_SHIFT", 1.5)
ANCHOR = (ANCHOR_WEIGHT, ANCHOR_MAX_SHIFT)

SIMS = int(_f("CBB_SIMS", 10000))
FIRST_GAMES = int(_f("CBB_FIRST_GAMES", 10))  # backtest window: each team's first N games
