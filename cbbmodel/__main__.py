"""python -m cbbmodel backfill SEASON [--odds-through YYYY-MM-DD] | backtest SEASON | shadow"""
import argparse
import json

from . import backtest, config, espn, shadow


def backfill(season, odds_through=None):
    d1 = shadow.teams()
    path = config.DATA_DIR / f"games_{season}.json.gz"
    old = {g["id"]: g for g in shadow.load(path, [])}
    games = espn.season_games(season, d1)
    for g in games:
        g.update({k: v for k, v in old.get(g["id"], {}).items() if k not in g})
    want = (lambda g: (g["start"] or "")[:10] <= odds_through and g["h"] in d1 and g["a"] in d1) if odds_through else (lambda g: False)
    espn.fill(games, with_odds=want)
    shadow.save(path, games)
    n_box = sum(1 for g in games if "hp" in g)
    n_odds = sum(1 for g in games if g.get("spread") is not None)
    print(f"season {season}: {len(games)} games, {n_box} with box scores, {n_odds} with lines")


def run_backtest(season):
    d1 = shadow.teams()
    prev = shadow.load(config.DATA_DIR / f"games_{season - 1}.json.gz", [])
    games = shadow.load(config.DATA_DIR / f"games_{season}.json.gz", [])
    res = {"season": season, "first_games": config.FIRST_GAMES}
    res["result"] = backtest.run(prev, games, d1)
    for k in (3, 8):  # how much the preseason prior should count
        config.PRIOR_GAMES = k
        res[f"prior_games_{k}"] = {x: res_ for x, res_ in backtest.run(prev, games, d1).items()
                                   if x in ("margin", "total", "plays")}
    shadow.save(config.DATA_DIR / "backtest.json", res)
    print(json.dumps(res, indent=1))


def main():
    ap = argparse.ArgumentParser(prog="cbbmodel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("season", type=int)
    b.add_argument("--odds-through")
    t = sub.add_parser("backtest")
    t.add_argument("season", type=int)
    sub.add_parser("shadow")
    a = ap.parse_args()
    if a.cmd == "backfill":
        backfill(a.season, a.odds_through)
    elif a.cmd == "backtest":
        run_backtest(a.season)
    else:
        shadow.run()


if __name__ == "__main__":
    main()
