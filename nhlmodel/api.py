"""NHL public API (api-web.nhle.com) access."""
import time

from sportscommon.http import fetch_json

from . import config


def get(path):
    time.sleep(config.REQUEST_PAUSE)
    return fetch_json(f"{config.API}/{path.lstrip('/')}")


def standings():
    return get("standings/now").get("standings", [])


def club_schedule(team, season):
    return get(f"club-schedule-season/{team}/{season}").get("games", [])


def play_by_play(game_id):
    return get(f"gamecenter/{game_id}/play-by-play")


def score(date_str):
    return get(f"score/{date_str}")


def partner_odds(country="US"):
    return get(f"partner-game/{country}/now")
