"""Tunable model and pipeline settings.

Most values can be overridden with environment variables (see README) so the
GitHub Actions workflows can be tweaked without code changes.
"""
import os
from pathlib import Path


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def _i(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return int(default)


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
SITE_DATA_DIR = ROOT / "site" / "data"

CFBD_BASE_URL = os.environ.get("CFBD_BASE_URL", "https://api.collegefootballdata.com")

# --- Power rating blend -----------------------------------------------------
# Each source is mapped onto the SP+ points scale (mean/std matched across FBS
# teams), then blended with these weights. Missing sources are dropped and the
# remaining weights renormalized.
WEIGHTS = {
    "sp": _f("CFB_W_SP", 0.40),
    "fpi": _f("CFB_W_FPI", 0.35),
    "elo": _f("CFB_W_ELO", 0.25),
}
# Points added to the home team's margin (0 at neutral sites).
HOME_FIELD = _f("CFB_HFA", 2.5)
# Rating given to teams with no FBS rating (mostly FCS opponents).
NON_FBS_RATING = _f("CFB_NON_FBS_RATING", -22.0)
# When the current season has no rating yet for a team, fall back to last
# season's value shrunk toward average by this factor.
PRIOR_SEASON_REGRESSION = _f("CFB_PRIOR_REGRESS", 0.65)
# Std. dev. of actual margin around projected margin (for win probability).
MARGIN_SD = _f("CFB_MARGIN_SD", 15.5)
# Fallback total when SP+ offense/defense are unavailable and no market total.
DEFAULT_TOTAL = _f("CFB_DEFAULT_TOTAL", 54.0)

# --- Picks ------------------------------------------------------------------
# Every game with a line gets an ATS side. Edge tiers drive the UI badges and
# the "best bets" record split.
LEAN_EDGE = _f("CFB_LEAN_EDGE", 2.0)
PLAY_EDGE = _f("CFB_PLAY_EDGE", 4.0)
STRONG_EDGE = _f("CFB_STRONG_EDGE", 7.0)

# Sportsbook priority for the "current line". If none of these are present the
# median across all posted books is used.
LINE_PROVIDERS = [
    p.strip()
    for p in os.environ.get(
        "CFB_LINE_PROVIDERS", "DraftKings,ESPN Bet,Bovada,consensus,William Hill (New Jersey)"
    ).split(",")
    if p.strip()
]

# --- Confidence --------------------------------------------------------------
# 0-100 score, separate from edge. Component weights (sum to 1):
CONF_WEIGHTS = {"agreement": 0.25, "consistency": 0.10, "edge": 0.25, "market": 0.15, "trends": 0.15, "data": 0.10}
CONF_HIGH = _f("CFB_CONF_HIGH", 70)
CONF_MEDIUM = _f("CFB_CONF_MEDIUM", 55)

# --- Weekly weight tuning ---------------------------------------------------
# After Sunday grading, refit SP+/FPI/Elo weights and home field on graded
# games, shrunk toward the current values: alpha = n / (n + TUNE_PRIOR_GAMES).
TUNING = os.environ.get("CFB_TUNING", "on") != "off"
MIN_TUNE_GAMES = _i("CFB_MIN_TUNE_GAMES", 40)
TUNE_PRIOR_GAMES = _f("CFB_TUNE_PRIOR_GAMES", 250)

# --- ATS trends -------------------------------------------------------------
TREND_SEASONS = _i("CFB_TREND_SEASONS", 6)  # completed seasons + current season
BYE_DAYS = _i("CFB_BYE_DAYS", 13)  # gap that counts as "off a bye"
TREND_MIN_GAMES = _i("CFB_TREND_MIN_GAMES", 8)  # min sample to call a trend notable
TREND_NOTABLE_PCT = _f("CFB_TREND_NOTABLE_PCT", 0.60)  # >= this or <= 1-this

# --- Bet splits -------------------------------------------------------------
SPLITS_PROVIDER = os.environ.get("SPLITS_PROVIDER", "none")  # none | file | http_json
SPLITS_FILE = Path(os.environ.get("SPLITS_FILE", str(DATA_DIR / "splits_manual.json")))
SPLITS_URL = os.environ.get("SPLITS_URL", "")
SPLITS_API_KEY = os.environ.get("SPLITS_API_KEY", "")
# Money% minus bet% (same side) at or above this flags "sharp money".
SHARP_GAP = _f("CFB_SHARP_GAP", 10.0)
# A side with at least this share of tickets is the "public side".
PUBLIC_BETS = _f("CFB_PUBLIC_BETS", 65.0)

# --- Notifications ----------------------------------------------------------
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")
BRIEFING_TZ = os.environ.get("BRIEFING_TZ", "America/Chicago")
BRIEFING_HOUR = _i("BRIEFING_HOUR", 9)
SITE_URL = os.environ.get("SITE_URL", "")
