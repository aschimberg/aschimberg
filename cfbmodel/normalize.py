"""Turn raw CFBD game/line records into compact dicts used everywhere else."""
from statistics import median

from . import config
from .cfbd import g


def num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize_game(raw):
    return {
        "id": g(raw, "id"),
        "season": g(raw, "season"),
        "week": g(raw, "week"),
        "season_type": g(raw, "seasonType", "regular"),
        "start": g(raw, "startDate"),
        "start_tbd": bool(g(raw, "startTimeTBD", False)),
        "completed": bool(g(raw, "completed", False)),
        "neutral": bool(g(raw, "neutralSite", False)),
        "conference_game": bool(g(raw, "conferenceGame", False)),
        "venue": g(raw, "venue"),
        "home": g(raw, "homeTeam"),
        "away": g(raw, "awayTeam"),
        "home_conf": g(raw, "homeConference"),
        "away_conf": g(raw, "awayConference"),
        "home_class": g(raw, "homeClassification") or g(raw, "homeDivision"),
        "away_class": g(raw, "awayClassification") or g(raw, "awayDivision"),
        "home_pts": num(g(raw, "homePoints")),
        "away_pts": num(g(raw, "awayPoints")),
    }


def select_line(book_lines, providers=None):
    """Pick the line to use from a CFBD `lines` array.

    Returns the first provider in `providers` order that has a spread, else the
    median spread across all books. All spreads are from the HOME team's
    perspective (negative = home favored).
    """
    providers = providers if providers is not None else config.LINE_PROVIDERS
    books = {}
    for ln in book_lines or []:
        spread = num(g(ln, "spread"))
        if spread is None:
            continue
        books[g(ln, "provider", "unknown")] = {
            "spread": spread,
            "spread_open": num(g(ln, "spreadOpen")),
            "total": num(g(ln, "overUnder")),
            "total_open": num(g(ln, "overUnderOpen")),
            "home_ml": num(g(ln, "homeMoneyline")),
            "away_ml": num(g(ln, "awayMoneyline")),
        }
    if not books:
        return None
    chosen = None
    for p in providers:
        if p in books:
            chosen = p
            break
    if chosen:
        out = dict(books[chosen])
        out["book"] = chosen
    else:
        spreads = [b["spread"] for b in books.values()]
        totals = [b["total"] for b in books.values() if b["total"] is not None]
        opens = [b["spread_open"] for b in books.values() if b["spread_open"] is not None]
        out = {
            "spread": round(median(spreads) * 2) / 2,
            "spread_open": round(median(opens) * 2) / 2 if opens else None,
            "total": round(median(totals) * 2) / 2 if totals else None,
            "total_open": None,
            "home_ml": None,
            "away_ml": None,
            "book": "median",
        }
    out["books"] = {k: v["spread"] for k, v in books.items()}
    return out


def closing_spread(book_lines):
    """Median spread across books — used for historical ATS grading."""
    spreads = [num(g(ln, "spread")) for ln in book_lines or []]
    spreads = [s for s in spreads if s is not None]
    if not spreads:
        return None
    return round(median(spreads) * 2) / 2


def lines_by_game(raw_lines):
    return {g(r, "id"): (g(r, "lines") or []) for r in raw_lines or []}
