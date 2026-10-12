"""CLI: python -m nflmodel {refresh,tune,backtest}"""
import argparse
import sys

from . import pipeline


def _seasons(text):
    a, _, b = text.partition("-")
    return list(range(int(a), int(b or a) + 1))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nflmodel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh", help="pull nflverse data, rate teams, price the slate, grade picks, write site data")
    sub.add_parser("tune", help="refit EPA/Elo weights and home field on graded picks")
    b = sub.add_parser("backtest", help="walk-forward dry run over past seasons")
    b.add_argument("--seasons", default="2023-2026", help="e.g. 2023-2026")
    b.add_argument("--apply", action="store_true", help="add current-season picks to the live log and adopt the tuned weights")
    args = ap.parse_args(argv)
    if args.cmd == "refresh":
        pipeline.refresh()
    elif args.cmd == "tune":
        pipeline.tune()
    elif args.cmd == "backtest":
        from . import backtest
        from .config import DEFAULT_WEIGHTS, DEFAULT_HOME_FIELD
        res = backtest.run(_seasons(args.seasons), start_weights=DEFAULT_WEIGHTS, start_hfa=DEFAULT_HOME_FIELD)
        backtest.save(res)
        s = res["summary"]
        for k, v in s.items():
            print(f"{k}: ATS {v['ats']['W']}-{v['ats']['L']}-{v['ats'].get('P', 0)} ({(v['ats'].get('pct') or 0) * 100:.1f}%), "
                  f"edge≥play {v['ats_play']['W']}-{v['ats_play']['L']}, strong {v['ats_strong']['W']}-{v['ats_strong']['L']}, "
                  f"SU {v['su']['W']}-{v['su']['L']}")
        print("final", res["final"])
        if args.apply:
            backtest.apply(res)
            pipeline.refresh()
    return 0


if __name__ == "__main__":
    sys.exit(main())
