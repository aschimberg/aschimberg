import os

os.environ.setdefault("NEWS", "off")  # tests never fetch news
# fewer simulated games per matchup in tests (production: 20,000)
for _k in ("NFL_SIMS", "CFB_SIMS", "NBA_SIMS", "NFL_SIMS_BACKTEST", "CFB_SIMS_BACKTEST", "NBA_SIMS_BACKTEST"):
    os.environ.setdefault(_k, "1500")
