"""CLI: python -m cfbmodel {refresh,grade,briefing,history}"""
import argparse
import json
import os
import sys
import time

from . import briefing, config, pipeline, store
from .cfbd import CFBDClient, QuotaExceeded

QUOTA_NOTE = ("::warning::CFBD monthly call quota is used up; the college football board keeps its last "
              "refresh until the quota resets on the 1st")


def _last_refresh_path():
    return config.DATA_DIR / "cache" / "cfbd" / "last_refresh.json"


def recently_refreshed():
    """A refresh within CFB_MIN_REFRESH_MIN minutes (default 150): back-to-back runs (a late
    scheduled run right after a manual one) would only spend API calls on the same data."""
    try:
        at = json.loads(_last_refresh_path().read_text())["at"]
    except (OSError, ValueError, KeyError):
        return False
    return time.time() - at < float(os.environ.get("CFB_MIN_REFRESH_MIN", 150)) * 60


def mark_refreshed():
    p = _last_refresh_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": int(time.time())}))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cfbmodel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("refresh", help="pull CFBD data, update ratings/picks/trends, write site data")
    rp.add_argument("--force", action="store_true", help="refresh even if the last one was minutes ago")
    gp = sub.add_parser("grade", help="grade logged picks and send the weekly recap")
    gp.add_argument("--no-recap", action="store_true")
    bp = sub.add_parser("briefing", help="send the game-day ntfy briefing")
    bp.add_argument("--force", action="store_true", help="ignore the time window / already-sent check")
    bp.add_argument("--dry-run", action="store_true", help="print instead of sending")
    bp.add_argument("--check", action="store_true",
                    help="only decide whether to send now; writes go=true/false to $GITHUB_OUTPUT")
    hp = sub.add_parser("history", help="backfill historical games + lines for ATS trends")
    hp.add_argument("--season", type=int, help="current season (default: inferred from today)")
    bt = sub.add_parser("backtest", help="walk-forward dry run over past seasons")
    bt.add_argument("--seasons", default="2024-2026")
    bt.add_argument("--apply", action="store_true", help="add this season's picks to the live log and adopt the backtest home field")
    bt.add_argument("--adopt-weights", action="store_true", help="with --apply, also adopt the backtest's source weights")
    args = ap.parse_args(argv)

    if args.cmd == "briefing":
        if args.check:
            go = briefing.check(force=args.force)
            out = os.environ.get("GITHUB_OUTPUT")
            if out:
                with open(out, "a") as fh:
                    fh.write(f"go={'true' if go else 'false'}\n")
            print(f"go={go}")
            return 0
        briefing.run_briefing(force=args.force, dry_run=args.dry_run)
        return 0

    offline = args.cmd == "backtest" and os.environ.get("CFB_BACKTEST_OFFLINE") == "1"
    client = None if offline else CFBDClient()
    if args.cmd == "refresh":
        if not args.force and recently_refreshed():
            print("CFB refreshed under 2.5 hours ago: skipping to save CFBD calls (use --force to override)")
            return 0
        try:
            pipeline.refresh(client)
        except QuotaExceeded:
            print(QUOTA_NOTE)
            return 0
        mark_refreshed()
        print(f"CFBD calls this refresh: {client.calls}")
    elif args.cmd == "grade":
        try:
            _, _, tuned = pipeline.grade_only(client)
        except QuotaExceeded:
            print(QUOTA_NOTE)
            return 0
        if not args.no_recap:
            picks = store.load(pipeline.PICKS_PATH, {"picks": {}})["picks"]
            last_week, rec = pipeline.last_week_summary(picks, pipeline.season_for(pipeline.utcnow()))
            briefing.run_recap(rec, last_week, tuned)
    elif args.cmd == "backtest":
        from . import backtest
        a, _, b = args.seasons.partition("-")
        res = backtest.run(list(range(int(a), int(b or a) + 1)), client,
                           start_weights=config.DEFAULT_WEIGHTS, start_hfa=config.DEFAULT_HOME_FIELD)
        backtest.save(res)
        for k, v in res["summary"].items():
            print(f"{k}: ATS {v['ats']['W']}-{v['ats']['L']}-{v['ats']['P']} ({(v['ats']['pct'] or 0) * 100:.1f}%), "
                  f"edge≥{config.PLAY_EDGE:g} {v['ats_play']['W']}-{v['ats_play']['L']}, "
                  f"edge≥{config.STRONG_EDGE:g} {v['ats_strong']['W']}-{v['ats_strong']['L']}, SU {v['su']['W']}-{v['su']['L']}")
        print("final", res["final"], f"({client.calls if client else 0} CFBD calls)")
        if args.apply:
            backtest.apply(res, adopt_weights=args.adopt_weights)
            pipeline.refresh(client)
    elif args.cmd == "history":
        season = args.season or pipeline.season_for(pipeline.utcnow())
        seasons = range(season - config.TREND_SEASONS, season)
        games = pipeline.load_history(client, seasons)
        print(f"{len(games)} historical games cached for {seasons.start}-{seasons.stop - 1} ({client.calls} calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
