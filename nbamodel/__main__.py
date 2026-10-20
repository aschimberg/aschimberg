"""CLI: python -m nbamodel {refresh,tune,backtest}"""
import argparse
import sys

from . import pipeline


def _seasons(text):
    a, _, b = text.partition("-")
    return list(range(int(a), int(b or a) + 1))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nbamodel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh", help="update games/box scores/odds/injuries from ESPN, rate teams, price the board, grade picks")
    sub.add_parser("tune", help="refit efficiency/points/Elo weights and home court on graded picks")
    b = sub.add_parser("backtest", help="walk-forward dry run over past seasons")
    b.add_argument("--seasons", default="2026", help="ESPN season years, e.g. 2026 (= 2025-26)")
    b.add_argument("--apply", action="store_true", help="adopt the tuned weights and home court for the live model")
    args = ap.parse_args(argv)
    if args.cmd == "refresh":
        pipeline.refresh()
    elif args.cmd == "tune":
        pipeline.tune()
    elif args.cmd == "backtest":
        from . import backtest
        from .config import DEFAULT_HOME_COURT, DEFAULT_WEIGHTS
        res = backtest.run(_seasons(args.seasons), start_weights=DEFAULT_WEIGHTS, start_hfa=DEFAULT_HOME_COURT)
        backtest.save(res)
        for k, v in res["summary"].items():
            print(f"{k}: ATS {v['ats']['W']}-{v['ats']['L']}-{v['ats'].get('P', 0)} ({(v['ats'].get('pct') or 0) * 100:.1f}%), "
                  f"lean {v['ats_lean']['W']}-{v['ats_lean']['L']}, play {v['ats_play']['W']}-{v['ats_play']['L']}, "
                  f"strong {v['ats_strong']['W']}-{v['ats_strong']['L']}, SU {v['su']['W']}-{v['su']['L']}, "
                  f"O/U {v['ou']['W']}-{v['ou']['L']}, O/U plays {v['ou_play']['W']}-{v['ou_play']['L']}")
        print("final", res["final"])
        if args.apply:
            backtest.apply(res)
            pipeline.refresh()
    return 0


if __name__ == "__main__":
    sys.exit(main())
