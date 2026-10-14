"""Game-day briefing and weekly recap via ntfy."""
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sportscommon import totals

from . import ats, config, splits, store
from .model import fmt_spread
from .pipeline import fmt_rec

NTFY_MAX_BYTES = 3800  # ntfy turns bodies > 4096 bytes into attachments


def local_date(iso_str, tz):
    dt = ats.parse_dt(iso_str)
    return dt.astimezone(tz).date() if dt else None


def kickoff(card, tz):
    if card.get("start_tbd"):
        return "TBD"
    dt = ats.parse_dt(card["start"])
    return dt.astimezone(tz).strftime("%-I:%M%p").lower() if dt else "TBD"


def matchup(card):
    sep = " vs " if card.get("neutral") else " @ "
    return f"{card['away']['name']}{sep}{card['home']['name']}"


def todays_games(cards, today, tz):
    return [c for c in cards if local_date(c["start"], tz) == today and not c["result"].get("completed")]


def _team_line(card, side):
    s = card["line"]["spread"]
    return s if side == "home" else -s


def build_briefing(meta, cards, record, today, tz, top_n=8):
    games = todays_games(cards, today, tz)
    if not games:
        return None
    sr = record.get("season_record") or {}
    lines = [f"**{today.strftime('%a %b %-d')} · {meta.get('slate_label', '')} · {len(games)} games**"]
    if sr.get("ats"):
        lines.append(
            f"Season: ATS {fmt_rec(sr['ats'])} · {config.PLAY_EDGE:g}+ edge {fmt_rec(sr['ats_play'])} · SU {fmt_rec(sr['su'], ties=False)}"
        )

    def conf_of(c):
        return c["pick"].get("confidence") or 0

    picks = sorted((c for c in games if c["pick"].get("edge") is not None),
                   key=lambda c: (-c["pick"]["edge"], -conf_of(c)))
    # Plays: a real edge, or High confidence even when the edge is small.
    plays = [c for c in picks if c["pick"]["edge"] >= config.LEAN_EDGE or c["pick"].get("conf_label") == "High"]
    lines.append("")
    lines.append("**🎯 Picks / biggest edges**")
    if not plays:
        lines.append("No edges ≥ {:g} pts or High-confidence sides today.".format(config.LEAN_EDGE))
    for c in plays[:top_n]:
        p, m = c["pick"], c["model"]
        tier = {"strong": "🔥", "play": "✅", "lean": "·"}.get(p["tier"], "🎯")
        conf = f", conf {p['confidence']} {p['conf_label']}" if p.get("confidence") is not None else ""
        lines.append(
            f"{tier} {p['ats_team']} {fmt_spread(p['ats_line'])} ({matchup(c)}, {kickoff(c, tz)}) "
            f"— model {c['home']['name']} {fmt_spread(m['spread'])}, edge {p['edge']:g}{conf}, proj {m['away_pts']:.0f}-{m['home_pts']:.0f}"
        )
    if len(plays) > top_n:
        lines.append(f"…plus {len(plays) - top_n} more on the site")

    lines += totals.briefing_lines(games, matchup, kickoff, tz)

    moves = sorted(
        (c for c in games if c.get("line") and c["line"].get("move") and abs(c["line"]["move"]) >= 1.0),
        key=lambda c: -abs(c["line"]["move"]),
    )
    lines.append("")
    lines.append("**📈 Line moves (home spread, open → now)**")
    if not moves:
        lines.append("No moves of 1+ pt.")
    for c in moves[:6]:
        ln = c["line"]
        toward = c["home"]["name"] if ln["move"] < 0 else c["away"]["name"]
        lines.append(f"{matchup(c)}: {fmt_spread(ln['open'])} → {fmt_spread(ln['spread'])} (toward {toward})")

    trend_items = []
    for c in games:
        for side in ("home", "away"):
            for t in c["trends"].get(side, []):
                if t.get("notable"):
                    trend_items.append((abs(t["z"]), c[side]["name"], t, c))
    trend_items.sort(key=lambda x: -x[0])
    lines.append("")
    lines.append(f"**📊 Key ATS trends ({meta['trend_seasons'][0]}–{meta['trend_seasons'][1]})**")
    if not trend_items:
        lines.append("Nothing notable.")
    per_team, shown = {}, 0
    for _, team, t, c in trend_items:
        if per_team.get(team, 0) >= 2:
            continue
        per_team[team] = per_team.get(team, 0) + 1
        p = f"-{t['p']}" if t["p"] else ""
        lines.append(f"{team} {t['w']}-{t['l']}{p} ATS {t['label'].lower()} ({t['pct'] * 100:.0f}%) — {matchup(c)}")
        shown += 1
        if shown >= 6:
            break

    lines.append("")
    lines.append("**💰 Bet splits**")
    split_games = [c for c in games if c.get("splits")]
    if meta.get("splits", {}).get("provider") in (None, "none"):
        lines.append("Splits feed not configured.")
    elif not split_games:
        lines.append("No splits available for today's games.")
    else:
        flagged = [c for c in split_games if c["splits"]["flags"]]
        flagged.sort(key=lambda c: -max(f["gap"] for f in c["splits"]["flags"]))
        for c in flagged[:6]:
            h, a = c["home"]["name"], c["away"]["name"]
            desc = "; ".join(splits.flag_text(f, h, a) for f in c["splits"]["flags"][:2])
            lines.append(f"{matchup(c)}: {desc}")
        if not flagged:
            lines.append("No sharp/public divergence flags today.")

    if meta.get("site_url"):
        lines.append("")
        lines.append(f"Full card: {meta['site_url']}")
    return "\n".join(lines)


def chunk(text, limit=NTFY_MAX_BYTES):
    """Split on line boundaries so each chunk stays under ntfy's size limit."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate.encode("utf-8")) > limit and cur:
            chunks.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def send_ntfy(title, body, tags="football", priority="default", click=None):
    if not config.NTFY_TOPIC:
        print("NTFY_TOPIC not set; printing instead:\n" + body)
        return False
    parts = chunk(body)
    for i, part in enumerate(parts, 1):
        t = title if len(parts) == 1 else f"{title} ({i}/{len(parts)})"
        # Title goes in the query string so emoji/UTF-8 survive (HTTP headers
        # are latin-1 only).
        params = {"title": t, "tags": tags, "priority": priority, "markdown": "yes"}
        if click:
            params["click"] = click
        headers = {"User-Agent": "cfb-model-site/1.0"}
        if config.NTFY_TOKEN:
            headers["Authorization"] = f"Bearer {config.NTFY_TOKEN}"
        url = f"{config.NTFY_SERVER}/{urllib.parse.quote(config.NTFY_TOPIC)}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, data=part.encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            print(f"ntfy: sent part {i}/{len(parts)} ({len(part.encode('utf-8'))} bytes), HTTP {resp.status}")
    return True


STATE_PATH = config.DATA_DIR / "briefing_state.json"
# GitHub cron can run late, and 9 AM CT is 14:00 UTC in CDT but 15:00 UTC in
# CST. The workflow fires at both; any run inside this window that hasn't
# already sent today's briefing sends it.
WINDOW_HOURS = 2


def gate(now_local, state):
    """Return (ok, reason)."""
    start = now_local.replace(hour=config.BRIEFING_HOUR, minute=0, second=0, microsecond=0)
    if not (start <= now_local < start.replace(hour=config.BRIEFING_HOUR + WINDOW_HOURS)):
        return False, f"local time {now_local:%H:%M %Z} outside {config.BRIEFING_HOUR}:00 window"
    if state.get("last_sent") == now_local.date().isoformat():
        return False, f"briefing already sent for {now_local.date()}"
    return True, "ok"


def load_slate():
    out = config.SITE_DATA_DIR
    return (
        store.load(out / "meta.json", {}),
        store.load(out / "slate.json", {}).get("games", []),
        store.load(out / "record.json", {}),
    )


def check(now=None, force=False):
    """Should the workflow refresh + send? True only on game days in the window."""
    tz = ZoneInfo(config.BRIEFING_TZ)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    if not force:
        ok, reason = gate(now, store.load(STATE_PATH, {}))
        if not ok:
            print(f"skip: {reason}")
            return False
    _, cards, _ = load_slate()
    games = todays_games(cards, now.date(), tz)
    print(f"{len(games)} games today ({now.date()})")
    return bool(games)


def run_briefing(now=None, force=False, dry_run=False):
    tz = ZoneInfo(config.BRIEFING_TZ)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    state = store.load(STATE_PATH, {})
    if not force:
        ok, reason = gate(now, state)
        if not ok:
            print(f"skip: {reason} (use --force to override)")
            return None
    meta, cards, record = load_slate()
    body = build_briefing(meta, cards, record, now.date(), tz)
    if body is None:
        print(f"No games on {now.date()}; no briefing sent")
        return None
    if dry_run:
        print(body)
        return body
    sent = send_ntfy("🏈 ASPicks · CFB game-day briefing", body, tags="football,chart_with_upwards_trend",
                     priority="high", click=meta.get("site_url") or None)
    if sent:
        store.save(STATE_PATH, {"last_sent": now.date().isoformat(), "sent_at": now.isoformat()})
    return body


def tuning_line(tuned):
    if not tuned:
        return None
    if tuned.get("status") != "updated":
        return f"**Weights:** unchanged ({tuned['games']} graded games so far; tuning starts at {config.MIN_TUNE_GAMES})"
    b, a = tuned["before"], tuned["after"]
    pct = lambda w: "/".join(f"{w[k] * 100:.0f}" for k in ("sp", "fpi", "elo"))
    return (f"**Weights (SP+/FPI/Elo):** {pct(b['weights'])} → {pct(a['weights'])} · "
            f"HFA {b['home_field']:g} → {a['home_field']:g} · RMSE {tuned['rmse_before']} → {tuned['rmse_after']} "
            f"on {tuned['games']} games")


def run_recap(record, last_week, tuned=None):
    if not last_week:
        return None
    sr = record
    title = f"ASPicks · CFB recap — {'Week ' + str(last_week['week']) if last_week['season_type'] == 'regular' else 'Postseason'}"
    bc = sr.get("ats_by_conf", {})
    body = "\n".join(x for x in [
        f"**Last week:** ATS {fmt_rec(last_week['ats'])} · {config.PLAY_EDGE:g}+ edge {fmt_rec(last_week['ats_plays'])} · SU {fmt_rec(last_week['su'], ties=False)}",
        f"**Season:** ATS {fmt_rec(sr['ats'])} · {config.PLAY_EDGE:g}+ edge {fmt_rec(sr['ats_play'])} · "
        f"{config.STRONG_EDGE:g}+ edge {fmt_rec(sr['ats_strong'])} · SU {fmt_rec(sr['su'], ties=False)}",
        f"**By confidence:** High {fmt_rec(bc['High'])} · Medium {fmt_rec(bc['Medium'])} · Low {fmt_rec(bc['Low'])}" if bc else None,
        tuning_line(tuned),
    ] if x)
    send_ntfy(title, body, tags="football,white_check_mark", click=config.SITE_URL or None)
    return body
