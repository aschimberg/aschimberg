"""Parse NHL play-by-play into a compact game record + unblocked shot rows.

Shot row (list): [side, period, sec, dist, angle, type, rebound, rush, pp, sh, en, goal, goalie]
  side: 1 = home shooter, 0 = away; en: shooting at an empty net; goalie: defending goalie id (0 if none)
"""
import math

SHOT_EVENTS = {"shot-on-goal", "missed-shot", "goal"}
SHOT_TYPES = ["wrist", "snap", "slap", "backhand", "tip-in", "deflected", "wrap-around"]
COLS = ["side", "period", "sec", "dist", "angle", "type", "rebound", "rush", "pp", "sh", "en", "goal", "goalie"]


def _secs(t):
    try:
        m, s = t.split(":")
        return int(m) * 60 + int(s)
    except (ValueError, AttributeError):
        return 0


def parse(pbp):
    home, away = pbp["homeTeam"], pbp["awayTeam"]
    hid = home["id"]
    shots = []
    starters = {}
    prev = None
    reg = {"home": 0, "away": 0}
    en_goals = {"home": 0, "away": 0}
    for p in pbp.get("plays", []):
        pd = p.get("periodDescriptor") or {}
        ptype = pd.get("periodType", "REG")
        period = pd.get("number", 1)
        sec = _secs(p.get("timeInPeriod"))
        d = p.get("details") or {}
        etype = p.get("typeDescKey")
        owner = d.get("eventOwnerTeamId")
        if ptype == "SO":
            continue
        if etype in SHOT_EVENTS and d.get("xCoord") is not None and owner is not None:
            is_home = owner == hid
            sc = p.get("situationCode") or "1551"
            if len(sc) != 4:
                sc = "1551"
            away_g, away_sk, home_sk, home_g = (int(c) for c in sc)
            own_sk, opp_sk, opp_g = (home_sk, away_sk, away_g) if is_home else (away_sk, home_sk, home_g)
            side = p.get("homeTeamDefendingSide")
            # Home attacks +x when it defends the left side; away the opposite.
            attack_pos = (side == "left") == is_home if side in ("left", "right") else d.get("xCoord", 0) >= 0
            net_x = 89 if attack_pos else -89
            x, y = d["xCoord"], d.get("yCoord", 0)
            dist = math.hypot(net_x - x, y)
            angle = math.degrees(math.atan2(abs(y), max(abs(net_x - x), 0.1)))
            goalie = d.get("goalieInNetId") or 0
            en = 1 if (opp_g == 0 or not goalie) else 0
            rebound = rush = 0
            if prev and prev["period"] == period and 0 <= sec - prev["sec"] <= 4:
                if prev["type"] in SHOT_EVENTS | {"blocked-shot"} and prev["owner"] == owner and sec - prev["sec"] <= 3:
                    rebound = 1
                elif (prev["owner"] == owner and prev["zone"] in ("N", "D")) or (prev["owner"] != owner and prev["zone"] == "O"):
                    rush = 1
            stype = d.get("shotType") or "wrist"
            tidx = SHOT_TYPES.index(stype) if stype in SHOT_TYPES else len(SHOT_TYPES)
            goal = 1 if etype == "goal" else 0
            shots.append([1 if is_home else 0, period, sec, round(dist, 1), round(angle, 1), tidx, rebound, rush,
                          int(own_sk > opp_sk), int(own_sk < opp_sk), en, goal, goalie])
            defending = "away" if is_home else "home"
            if goalie and defending not in starters:
                starters[defending] = goalie
            if goal:
                key = "home" if is_home else "away"
                if en:
                    en_goals[key] += 1
                elif ptype == "REG":
                    reg[key] += 1
        if etype not in ("stoppage", "period-start", "period-end"):
            prev = {"period": period, "sec": sec, "type": etype, "owner": owner, "zone": d.get("zoneCode")}
    names = {}
    for r in pbp.get("rosterSpots", []):
        if r.get("positionCode") == "G":
            first = (r.get("firstName") or {}).get("default", "")
            names[str(r["playerId"])] = f"{first[:1]}. {(r.get('lastName') or {}).get('default', '')}".strip(". ")
    outcome = (pbp.get("gameOutcome") or {}).get("lastPeriodType", "REG")
    return {
        "id": pbp["id"], "date": pbp.get("gameDate"), "start": pbp.get("startTimeUTC"), "type": pbp.get("gameType"),
        "home": home["abbrev"], "away": away["abbrev"], "home_score": home.get("score"), "away_score": away.get("score"),
        "last_period": outcome, "reg_home": reg["home"], "reg_away": reg["away"],
        "en_home": en_goals["home"], "en_away": en_goals["away"],
        "home_goalie": starters.get("home"), "away_goalie": starters.get("away"), "goalies": names, "shots": shots,
    }
