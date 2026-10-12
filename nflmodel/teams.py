"""NFL team reference (nflverse abbreviations)."""

TEAMS = {
    "ARI": ("Arizona Cardinals", "NFC West"), "ATL": ("Atlanta Falcons", "NFC South"),
    "BAL": ("Baltimore Ravens", "AFC North"), "BUF": ("Buffalo Bills", "AFC East"),
    "CAR": ("Carolina Panthers", "NFC South"), "CHI": ("Chicago Bears", "NFC North"),
    "CIN": ("Cincinnati Bengals", "AFC North"), "CLE": ("Cleveland Browns", "AFC North"),
    "DAL": ("Dallas Cowboys", "NFC East"), "DEN": ("Denver Broncos", "AFC West"),
    "DET": ("Detroit Lions", "NFC North"), "GB": ("Green Bay Packers", "NFC North"),
    "HOU": ("Houston Texans", "AFC South"), "IND": ("Indianapolis Colts", "AFC South"),
    "JAX": ("Jacksonville Jaguars", "AFC South"), "KC": ("Kansas City Chiefs", "AFC West"),
    "LA": ("Los Angeles Rams", "NFC West"), "LAC": ("Los Angeles Chargers", "AFC West"),
    "LV": ("Las Vegas Raiders", "AFC West"), "MIA": ("Miami Dolphins", "AFC East"),
    "MIN": ("Minnesota Vikings", "NFC North"), "NE": ("New England Patriots", "AFC East"),
    "NO": ("New Orleans Saints", "NFC South"), "NYG": ("New York Giants", "NFC East"),
    "NYJ": ("New York Jets", "AFC East"), "PHI": ("Philadelphia Eagles", "NFC East"),
    "PIT": ("Pittsburgh Steelers", "AFC North"), "SEA": ("Seattle Seahawks", "NFC West"),
    "SF": ("San Francisco 49ers", "NFC West"), "TB": ("Tampa Bay Buccaneers", "NFC South"),
    "TEN": ("Tennessee Titans", "AFC South"), "WAS": ("Washington Commanders", "NFC East"),
}
# Historical abbreviations in nflverse data.
ALIASES = {"OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA"}
_ESPN = {"LA": "lar", "WAS": "wsh"}


def norm(abbr):
    return ALIASES.get(abbr, abbr)


def name(abbr):
    return TEAMS.get(norm(abbr), (abbr, None))[0]


def division(abbr):
    return TEAMS.get(norm(abbr), (None, None))[1]


def logo(abbr):
    a = norm(abbr)
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{_ESPN.get(a, a.lower())}.png"
