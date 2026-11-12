import os

os.environ.setdefault("NEWS", "off")  # tests never fetch news
os.environ.setdefault("POLYMARKET", "off")  # or Polymarket prices (tests patch the fetch where needed)
# fewer simulated games per matchup in tests (production: 25,000-50,000)
for _k in ("NFL_SIMS", "CFB_SIMS", "NBA_SIMS", "NFL_SIMS_BACKTEST", "CFB_SIMS_BACKTEST", "NBA_SIMS_BACKTEST"):
    os.environ.setdefault(_k, "1500")
