"""CLI: python -m cfbmodel {refresh,grade,briefing,history}"""
import argparse
import os
import sys

from . import briefing, config, pipeline, store
from .cfbd import CFBDClient


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cfbmodel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh", help="pull CFBD data, update ratings/picks/trends, write site data")
    gp = sub.add_parser("grade", help="grade logged picks and send the weekly recap")
    gp.add_argument("--no-recap", action="store_true")
    bp = sub.add_parser("briefing", help="send the game-day ntfy briefing")
    bp.add_argument("--force", action="store_true", help="ignore the time window / already-sent check")
    bp.add_argument("--dry-run", action="store_true", help="print instead of sending")
    bp.add_argument("--check", action="store_true",
                    help="only decide whether to send now; writes go=true/false to $GITHUB_OUTPUT")
    hp = sub.add_parser("history", help="backfill historical games + lines for ATS trends")
    hp.add_argument("--season", type=int, help="current season (default: inferred from today)")
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

    client = CFBDClient()
    if args.cmd == "refresh":
        pipeline.refresh(client)
    elif args.cmd == "grade":
        pipeline.grade_only(client)
        if not args.no_recap:
            picks = store.load(pipeline.PICKS_PATH, {"picks": {}})["picks"]
            last_week, rec = pipeline.last_week_summary(picks, pipeline.season_for(pipeline.utcnow()))
            briefing.run_recap(rec, last_week)
    elif args.cmd == "history":
        season = args.season or pipeline.season_for(pipeline.utcnow())
        seasons = range(season - config.TREND_SEASONS, season)
        games = pipeline.load_history(client, seasons)
        print(f"{len(games)} historical games cached for {seasons.start}-{seasons.stop - 1} ({client.calls} calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
