"""Game-day ntfy briefings for NFL and NHL, plus a combined all-sports gate.

    python -m sportscommon.briefings check [--force]   # writes go/go_cfb/go_nfl/go_nhl to $GITHUB_OUTPUT
    python -m sportscommon.briefings send nfl|nhl [--force] [--dry-run]

CFB keeps its own briefing (python -m cfbmodel briefing); this module only
adds the CFB decision to the combined check.
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from cfbmodel import briefing as cfbb
from cfbmodel import config as cfbc
from cfbmodel import store
from nflmodel import config as nflc
from nhlmodel import config as nhlc
from sportscommon import totals
from sportscommon.odds import fmt_american

SPORTS = {"nfl": nflc, "nhl": nhlc}


def _local_now(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(cfbc.BRIEFING_TZ))


def _state_path(sport):
    return SPORTS[sport].DATA_DIR / "briefing_state.json"


def _load(sport):
    d = SPORTS[sport].SITE_DATA_DIR
    return store.load(d / "meta.json", {}), store.load(d / "slate.json", {}).get("games", []), store.load(d / "record.json", {})


def todays(sport, now_local):
    _, cards, _ = _load(sport)
    return cfbb.todays_games(cards, now_local.date(), now_local.tzinfo)


def due(sport, now_local, force=False):
    if not force:
        ok, reason = cfbb.gate(now_local, store.load(_state_path(sport), {}))
        if not ok:
            return False, reason
    games = todays(sport, now_local)
    return bool(games), f"{len(games)} {sport.upper()} games today"


def _rec(r, units=False):
    if not r:
        return "—"
    s = f"{r['W']}-{r['L']}" + (f"-{r['P']}" if r.get("P") else "")
    if units and r.get("units") is not None:
        s += f" ({r['units']:+.1f}u)"
    elif r.get("pct") is not None:
        s += f" ({r['pct'] * 100:.1f}%)"
    return s


def build_nfl(now_local):
    meta, _, record = _load("nfl")
    games = todays("nfl", now_local)
    if not games:
        return None
    tz = now_local.tzinfo
    sr = record.get("season_record") or {}
    lines = [f"**{now_local:%a %b %-d} · NFL {meta.get('slate_label', '')} · {len(games)} game{'s' if len(games) > 1 else ''}**"]
    if sr.get("ats", {}).get("W", 0) + sr.get("ats", {}).get("L", 0):
        lines.append(f"Season: ATS {_rec(sr['ats'])} · edge {nflc.PLAY_EDGE:g}+ {_rec(sr['ats_play'])} · SU {_rec(sr['su'])}")
    picks = [c for c in games if c["pick"].get("edge") is not None]
    plays = sorted((c for c in picks if c["pick"]["edge"] >= nflc.LEAN_EDGE or c["pick"].get("conf_label") == "High"),
                   key=lambda c: -c["pick"]["edge"])
    lines += ["", "**🎯 Picks**"]
    if not plays:
        lines.append(f"No edges ≥ {nflc.LEAN_EDGE:g} or High-confidence sides.")
    for c in plays[:8]:
        p = c["pick"]
        icon = {"strong": "🔥", "play": "✅", "lean": "·"}.get(p["tier"], "🎯")
        line = p["ats_line"]
        lt = "PK" if line == 0 else f"{line:+g}"
        cp = f", cover {p['cover_prob'] * 100:.0f}%" if p.get("cover_prob") else ""
        lines.append(f"{icon} {p['ats_team']} {lt} ({cfbb.matchup(c)}, {cfbb.kickoff(c, tz)}) — edge {p['edge']:g}{cp}, conf {p.get('confidence')} {p.get('conf_label')}")
    lines += totals.briefing_lines(games, cfbb.matchup, cfbb.kickoff, tz)
    qb = [e for c in games for e in c.get("extras", []) if " QB " in e and "(" in e]
    if qb:
        lines += ["", "**🏈 QB adjustments**"] + qb[:6]
    moves = sorted((c for c in games if (c.get("line") or {}).get("move")), key=lambda c: -abs(c["line"]["move"]))
    if moves:
        lines += ["", "**📈 Line moves**"]
        for c in moves[:5]:
            ln = c["line"]
            fav = lambda s: "PK" if not s else (f"{c['home']['name']} {s:g}" if s < 0 else f"{c['away']['name']} {-s:g}")
            lines.append(f"{cfbb.matchup(c)}: {fav(ln['open'])} → {fav(ln['spread'])}")
    trend_items = []
    for c in games:
        for side in ("home", "away"):
            for t in (c.get("trends") or {}).get(side, []):
                if t.get("notable"):
                    trend_items.append((abs(t["z"]), c[side]["name"], t, c))
    if trend_items:
        lines += ["", "**📊 Key ATS trends**"]
        for _, team, t, c in sorted(trend_items, key=lambda x: -x[0])[:5]:
            lines.append(f"{team} {t['w']}-{t['l']} ATS {t['label'].lower()} ({t['pct'] * 100:.0f}%)")
    if meta.get("site_url"):
        lines += ["", f"Full card: {meta['site_url']}nfl/"]
    return "\n".join(lines)


def build_nhl(now_local):
    meta, _, record = _load("nhl")
    games = todays("nhl", now_local)
    if not games:
        return None
    tz = now_local.tzinfo
    sr = record.get("season_record") or {}
    plays = sorted((c for c in games if (c["pick"].get("ev") or 0) >= nhlc.LEAN_EV or c["pick"].get("conf_label") == "High"),
                   key=lambda c: -(c["pick"].get("ev") or 0))
    totals = [c for c in games if (c["pick"].get("total_pick") or {}).get("play")]
    if not plays and not totals:
        return None  # quiet day: no value on the board
    lines = [f"**{now_local:%a %b %-d} · NHL · {len(games)} games**"]
    if sr.get("ats", {}).get("W", 0) + sr.get("ats", {}).get("L", 0):
        lines.append(f"Season ML {_rec(sr['ats'], True)} · EV {nhlc.PLAY_EV * 100:g}%+ {_rec(sr['ats_play'], True)}")
    lines += ["", "**🎯 Moneyline value**"]
    for c in plays[:8]:
        p = c["pick"]
        icon = {"strong": "🔥", "play": "✅", "lean": "·"}.get(p["tier"], "🎯")
        lines.append(f"{icon} {p['ats_team']} {fmt_american(p['odds'])} ({cfbb.matchup(c)}, {cfbb.kickoff(c, tz)}) — "
                     f"model {p['prob'] * 100:.0f}% vs mkt {p.get('mkt_prob', 0) * 100:.0f}%, EV {p['edge']:+.1f}%, conf {p.get('confidence')}")
    if not plays:
        lines.append("No moneyline value today.")
    if totals:
        lines += ["", "**🥅 Totals**"]
        for c in totals[:5]:
            t = c["pick"]["total_pick"]
            lines.append(f"{cfbb.matchup(c)}: {t['side'].title()} {t['line']:g} {fmt_american(t['odds'])} (EV {t['ev'] * 100:+.1f}%)")
    notes = [e for c in games for e in c.get("extras", []) if "backup" in e or "back-to-back" in e]
    if notes:
        lines += ["", "**🧤 Goalies & schedule**"] + notes[:8]
    if meta.get("site_url"):
        lines += ["", f"Full card: {meta['site_url']}nhl/"]
    return "\n".join(lines)


BUILDERS = {"nfl": build_nfl, "nhl": build_nhl}
TITLES = {"nfl": "🏈 NFL game-day briefing", "nhl": "🏒 NHL briefing"}


def send(sport, now=None, force=False, dry_run=False):
    now_local = _local_now(now)
    ok, reason = due(sport, now_local, force)
    if not ok:
        print(f"{sport}: skip ({reason})")
        return None
    if sport == "nhl" and not any(c.get("line") for c in todays(sport, now_local)):
        # DraftKings lines for today aren't in the NHL feed yet; a later run retries
        print("nhl: today's odds not posted yet")
        return None
    body = BUILDERS[sport](now_local)
    if body is None:
        print(f"{sport}: nothing worth sending today")
        store.save(_state_path(sport), {"last_sent": now_local.date().isoformat(), "skipped": True})
        return None
    if dry_run:
        print(body)
        return body
    if cfbb.send_ntfy(TITLES[sport], body, tags="sports", priority="high", click=None):
        store.save(_state_path(sport), {"last_sent": now_local.date().isoformat(), "sent_at": now_local.isoformat()})
    return body


def check(now=None, force=False):
    now_local = _local_now(now)
    flags = {"cfb": cfbb.check(now=now, force=force)}
    for sport in SPORTS:
        ok, reason = due(sport, now_local, force)
        print(f"{sport}: {reason}")
        flags[sport] = ok
    return flags


def main(argv=None):
    ap = argparse.ArgumentParser(prog="briefings")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--force", action="store_true")
    s = sub.add_parser("send")
    s.add_argument("sport", choices=sorted(SPORTS))
    s.add_argument("--force", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "check":
        flags = check(force=args.force)
        out = os.environ.get("GITHUB_OUTPUT")
        lines = [f"go_{k}={'true' if v else 'false'}" for k, v in flags.items()] + [f"go={'true' if any(flags.values()) else 'false'}"]
        if out:
            with open(out, "a") as fh:
                fh.write("\n".join(lines) + "\n")
        print("\n".join(lines))
    else:
        send(args.sport, force=args.force, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
