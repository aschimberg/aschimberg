"""CLI: python -m nflmodel {refresh,tune}"""
import argparse
import sys

from . import pipeline


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nflmodel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh", help="pull nflverse data, rate teams, price the slate, grade picks, write site data")
    sub.add_parser("tune", help="refit EPA/Elo weights and home field on graded picks")
    args = ap.parse_args(argv)
    if args.cmd == "refresh":
        pipeline.refresh()
    elif args.cmd == "tune":
        pipeline.tune()
    return 0


if __name__ == "__main__":
    sys.exit(main())
