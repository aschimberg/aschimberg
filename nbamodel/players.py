"""Player values and the injury adjustment.

A player's value is his Game Score per minute (this season, last season at
half weight, shrunk toward replacement level by PLAYER_PRIOR_MIN minutes)
above replacement, times his usual minutes, in points of margin:

    value = PLAYER_K * (rate - replacement) * expected minutes

Team ratings already reflect whoever has been playing, so an absence only
moves the line by how much tonight differs from the team's recent normal:

    adjustment = -sum(value * (recent availability - expected availability tonight))

Recent availability is the share of the team's last AVAIL_GAMES games he
played. Tonight's is 1 - P(miss) from the injury report (Out = 1.0, Doubtful
0.8, Questionable 0.5, Day-To-Day 0.35). A star who has missed the last ten
games and is listed Out moves nothing; one who just got hurt moves it fully.
"""
import re
import unicodedata

from . import config


def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def team_history(games):
    """{team: [(date, set(player ids who played)), ...] newest first} from box scores."""
    out = {}
    for g in sorted((g for g in games if g.get("box")), key=lambda g: g.get("start") or "", reverse=True):
        played = {}
        for pid, _n, ab, mins, _gs, _st in g["box"].get("players") or []:
            if mins and mins > 0:
                played.setdefault(ab, set()).add(pid)
        for t in (g["home"], g["away"]):
            out.setdefault(t, []).append((g["date"], played.get(t, set())))
    return out


def values(cur_games, prev_games):
    """{pid: {'name','team','value','exp_min','rate','games'}} and the league Game Score/min."""
    tot_gs = tot_min = 0.0
    acc = {}
    for games, w in ((prev_games, 0.5), (cur_games, 1.0)):
        for g in sorted((g for g in games if g.get("box")), key=lambda g: g.get("start") or ""):
            for pid, name, ab, mins, gs, _st in g["box"].get("players") or []:
                if not mins:
                    continue
                tot_gs += gs
                tot_min += mins
                a = acc.setdefault(pid, {"name": name, "team": ab, "gs": 0.0, "min": 0.0, "recent": [], "games": 0})
                a["gs"] += w * gs
                a["min"] += w * mins
                a["team"], a["name"] = ab, name or a["name"]  # latest game wins
                a["recent"].append(mins)
                a["games"] += 1
    league = tot_gs / tot_min if tot_min else 0.33
    repl = config.REPLACEMENT_FRAC * league
    out = {}
    for pid, a in acc.items():
        rate = (a["gs"] + config.PLAYER_PRIOR_MIN * repl) / (a["min"] + config.PLAYER_PRIOR_MIN)
        last = a["recent"][-10:]
        exp_min = min(40.0, sum(last) / len(last)) if last else 0.0
        v = config.PLAYER_K * (rate - repl) * exp_min
        out[pid] = {"name": a["name"], "team": a["team"], "value": round(max(-2.0, min(config.PLAYER_MAX, v)), 2),
                    "exp_min": round(exp_min, 1), "rate": round(rate, 3), "games": a["games"]}
    return out, league


def recent_availability(hist, pid, n=None):
    n = n or config.AVAIL_GAMES
    games = hist[:n]
    if not games:
        return 0.0
    return sum(1 for _, s in games if pid in s) / len(games)


def miss_prob(status):
    s = (status or "").strip().lower()
    for k, v in config.STATUS_MISS.items():
        if s.startswith(k):
            return v
    return 0.5


def adjustment(team, vals, hist, expected):
    """Points of margin for `team` tonight, plus notes.

    expected: {pid: P(plays tonight)} for players whose status is known
    (injury report live; actual box score in backtests). Players not listed
    keep their recent availability (no change).
    """
    adj, notes = 0.0, []
    th = hist.get(team) or []
    for pid, p_play in expected.items():
        v = vals.get(pid)
        if not v or v["team"] != team or v["exp_min"] < config.ROTATION_MIN or v["value"] <= 0:
            continue
        recent = recent_availability(th, pid)
        delta = v["value"] * (p_play - recent)
        if abs(delta) < 0.05:
            continue
        adj += delta
        notes.append((v["name"], round(delta, 1), p_play, recent))
    adj = max(-config.TEAM_ADJ_MAX, min(config.TEAM_ADJ_MAX, adj))
    notes.sort(key=lambda x: abs(x[1]), reverse=True)
    return round(adj, 2), notes


def expected_from_report(team, report, vals):
    """Injury report rows for a team -> {pid: P(plays)}; names resolve to ids when ESPN omits them."""
    by_name = {norm_name(v["name"]): pid for pid, v in vals.items() if v["team"] == team}
    out = {}
    for r in report or []:
        pid = r.get("id")
        if pid not in vals:
            pid = by_name.get(norm_name(r.get("name")))
        if pid:
            out[pid] = 1.0 - miss_prob(r.get("status"))
    return out


def expected_from_box(g, team, vals):
    """Backtests: the team's rotation players who sat out that game, as an injury report would list them.

    Only absences are used (P(plays) = 0), like the live report: a player who
    returns simply drops off the report and keeps his recent availability.
    """
    played = {pid for pid, _n, ab, mins, _gs, _st in (g.get("box") or {}).get("players") or [] if ab == team and mins}
    return {pid: 0.0 for pid, v in vals.items() if v["team"] == team and pid not in played}
