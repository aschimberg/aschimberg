"""CLI: python -m nhlmodel {refresh,tune}"""
import argparse
import sys

from . import pipeline


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nhlmodel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh", help="update games/shots from the NHL API, rate teams, price the board, grade picks")
    sub.add_parser("tune", help="refit xG/goals/Elo weights and home ice; retrain the xG model")
    args = ap.parse_args(argv)
    if args.cmd == "refresh":
        pipeline.refresh()
    elif args.cmd == "tune":
        pipeline.tune()
    return 0


if __name__ == "__main__":
    sys.exit(main())
