# CFB / NFL / NHL Model

A static college football model site on GitHub Pages, driven by
[CollegeFootballData.com](https://collegefootballdata.com) and GitHub Actions.
The visual design follows [davidsasser.com/cfb](https://davidsasser.com/cfb): warm paper background, square ruled cards, a deep-green accent, and Geist / Geist Mono type.

- **Power ratings**: SP+, ESPN FPI and CFBD Elo are rescaled onto the SP+ points scale and blended (40/35/25 by default).
- **Projections**: margin = rating diff + home field (2.5, 0 at neutral sites). Totals come from SP+ offense/defense. Output is a projected score, model spread and win probability.
- **Picks**: an ATS side against the current line for every game, plus the edge (expected cover margin) and a tier: lean ≥ 2, play ≥ 4, strong ≥ 7. Every game also gets an SU pick. Picks lock at kickoff and are graded SU and ATS.
- **Confidence**: a 0–100 score per pick, separate from edge. It combines how many of SP+/FPI/Elo cover on their own and how tightly they agree, the edge, line movement and sharp flags, situational ATS trends, and data quality. High is 70+ and Medium is 55+. A small-edge pick can still be High, and game-day briefings include High-confidence sides even when the edge is small. The Record tab tracks ATS results by confidence level.
- **Weekly tuning**: every pick stores each source's projected margin. After Sunday grading, the model fits the SP+/FPI/Elo weights and home field that best predict this season's actual margins, then moves part of the way there: alpha = n/(n+250), so it moves more as games accumulate. Tuning starts after 25 graded games. Each week the live weights are set to `(1 - alpha) * season-start weights + alpha * fit` on all of this season's games, so weekly steps don't compound. The current weights and their history are saved in `data/model_weights.json`, shown on the Method tab and included in the Sunday recap. Set `CFB_TUNING=off` to freeze the weights.
- **ATS trends**: closing lines and results for the last 6 seasons plus the current one. Each team's ATS record is split overall, as favorite or underdog, home/away/neutral (and combined, e.g. road dog), in conference play, off a bye (13+ days) and over the last 10 games. Each game card shows the splits that apply to that game.
- **Bet splits**: public bet % and money % per side, with sharp/public/reverse-line-move flags. The feed is swappable (see below).
- **ntfy briefing**: sent at 9 AM CT on game days with picks, biggest edges, line moves, key ATS trends and bet splits. A recap goes out after Sunday grading.

## NFL and NHL models

The same site serves `/nfl/` and `/nhl/` (switch sports in the header). Both run daily inside the **Daily refresh** workflow and work without API keys. An optional [The Odds API](https://the-odds-api.com) key adds multi-book lines (see below).

**NFL** (`nflmodel/`, data from [nflverse](https://github.com/nflverse), CC BY 4.0: schedules, consensus lines, projected starting QBs, play-by-play):
- **Team strength:** opponent-adjusted EPA/play on offense and defense, fit with weighted ridge regression. Recent weeks count more and garbage time is excluded. Ratings are shrunk toward last season.
- **QB adjustment:** the starter's shrunk EPA per dropback, compared with the QB play the team's rating was built on. Starters come from ESPN's current depth chart (QB1), skipping anyone ESPN's injury report lists as Out, Doubtful or on IR. If ESPN is unavailable, nflverse's projection is used and flagged when it differs from the team's last starter. The check reruns before each kickoff window.
- **Elo:** a 538-style rating from game results, as a second source.
- **Home field and rest:** about 1.6 points of home field (tuned weekly) and a tiny rest term.
- **Cover probability:** uses a margin distribution reweighted to real NFL final margins, so key numbers like 3 and 7 and pushes are priced correctly.
- **Picks:** ATS edges with tiers, confidence, a weekly record, ATS trends (with division games) and an ntfy briefing on game days.

**NHL** (`nhlmodel/`, data from the NHL public API (schedule, play-by-play, DraftKings odds), [MoneyPuck.com](https://moneypuck.com) (team xG, goalie GSAx; non-commercial use with credit) and ESPN (probable goalies)):
- **Expected goals:** our own xG model, a logistic regression on every unblocked shot (distance, angle, shot type, rebound, rush, strength). It is retrained weekly.
- **Team strength:** opponent-adjusted xG and goals per game, blended with Elo and with MoneyPuck's xG. MoneyPuck's model adds shooter talent and adjusts for flurries, score state and venue.
- **Goalies:** GSAx (goals saved above expected) for the starter. The starter is ESPN's listed probable goalie once it's posted (usually the morning of the game). Until then it's projected from recent starts, with the backup assumed on the second night of a back-to-back. Cards mark each goalie as probable or projected.
- **Back-to-backs:** a penalty for the second night.
- **Score distribution:** regulation Poisson, plus empty-net goals and an overtime/shootout step. This gives win, puck-line and total probabilities.
- **Picks:** moneyline value picks by EV%, totals at 3%+ EV, records in units, and a briefing only on days with value.

Tuning: NHL weights and home ice are refit on Mondays and the xG model is retrained; NFL weights and home field are refit on Tuesdays. NHL play-by-play is cached in `data/nhl/cache/` (the first run backfills last season, about 8 minutes).

## Backtests

`python -m nflmodel backtest --seasons 2023-2026` and `python -m cfbmodel backtest --seasons 2024-2026` replay each past week using only data available before kickoff. Each week is priced against the closing line with the live pick and confidence logic, then the weekly tuner runs, just as it does live. Results appear on each sport's **Record → Backtest** section: per-season and per-week records, the weights after every weekly tune, and every pick. You can also run them from Actions → **Backtest** (`apply` folds them into the live model).

- **NFL:**
  - EPA ratings are refit from earlier weeks' play-by-play; Elo is computed game by game in order.
  - QB values use only earlier dropbacks, combined with the game's actual starter.
  - `--apply` adds this season's backtest picks to the live log (tagged **BT**) and adopts the tuned EPA/Elo weights and home field.
- **CFB:**
  - CFBD serves SP+ and FPI only as end-of-season numbers, so each past week uses:
    - CFBD Elo as of the prior week;
    - ESPN's pregame win probability (FPI-based), converted to a margin;
    - an SP+ stand-in: opponent-adjusted scoring margin shrunk toward last season's SP+.
  - `--apply` adds this season's backtest picks and adopts the backtest's Elo weight, since that is the same CFBD Elo the live model uses.
  - The SP+/FPI split and home field stay with live tuning, and backtest picks are excluded from it, because their SP+/FPI columns are stand-ins.

## The Odds API (optional)

Add a repository secret `ODDS_API_KEY` to turn this on. With no key, the free sources are used.

- **NFL:** the line becomes the consensus (median) spread and total across US books, priced at DraftKings (then FanDuel, BetMGM, Caesars). Cards show the best available number for the pick side.
- **NHL:** fills in moneylines and totals for games DraftKings hasn't posted in the NHL feed yet, and shows the best price for the pick.
- **Credits:**
  - Each call costs markets × regions (2 credits here).
  - Responses are cached for 6 hours per sport (`NFL_ODDS_API_MAX_AGE`, `NHL_ODDS_API_MAX_AGE`), which works out to about 200–250 credits a month, under the free plan's 500.
  - Calls stop once the remaining balance reaches `ODDS_API_RESERVE` (default 40).
  - The balance is logged in `data/oddsapi/usage.json`.
- **Books:** set `ODDS_API_BOOKS` (comma-separated book keys) to change the preferred book order.

## Setup

1. **Secrets** (Settings → Secrets and variables → Actions → *Secrets*):
   - `CFBD_API_KEY` (required): your CollegeFootballData key.
   - `NTFY_TOPIC` (required for push): your ntfy topic name. Pick a hard-to-guess one, because anyone who knows a public ntfy.sh topic can read it.
   - `NTFY_TOKEN` (optional): an access token if your topic is protected.
   - `SPLITS_API_KEY` (optional): a bearer token for an `http_json` splits feed.
   - `ODDS_API_KEY` (optional): The Odds API key for multi-book NFL/NHL lines.
2. **Variables** (optional, *Variables* tab): `SITE_URL`, `NTFY_SERVER`, `SPLITS_PROVIDER` (`none` | `file` | `http_json`), `SPLITS_URL`.
3. **Pages**: Settings → Pages → Source: **GitHub Actions**.
4. **Default branch**: scheduled workflows only run on the default branch, and the `github-pages` environment only deploys from it by default. Merge this work into `main` and make `main` the default branch.
5. Run **Daily refresh** once from the Actions tab. The first run backfills 6 seasons of games and lines (about 18 CFBD calls) into `data/cache/`. Later runs use about 8–10 calls a day.

## Schedules

| Workflow | When | What |
|---|---|---|
| `refresh.yml` | daily 11:00 UTC | ratings, lines, projections, picks (re-priced until kickoff), trends, splits, grading → commit → deploy |
| `grade.yml` | Sundays 12:00 UTC | grades SU and ATS for every finished pick, rebuilds the record, sends an ntfy recap |
| `briefing.yml` | every 30 min, 14:07–16:37 UTC | sends each sport's ntfy briefing once per game day, between 9 and 11 AM CT (covers CDT/CST and late cron starts). The NHL briefing waits until that day's odds are posted and only goes out when there's value |
| `pregame.yml` | daily 21:17 UTC; Sun 16:17 + 19:47 UTC; Sun/Mon/Thu 23:47 UTC | re-checks lineups (NFL starting QBs, NHL probable goalies) and re-prices before kickoff / puck drop; picks up the day's DraftKings NHL lines |
| `deploy.yml` | after each job above, or on push to `main` touching `site/` | publishes `site/` to Pages |
| `ci.yml` | push / PR | unit tests and JS syntax check |

Manual runs: Actions → *Game-day briefing* → Run workflow → `force` sends the briefing right away.

## Bet splits: data source

I didn't find a free source whose terms allow automated collection of public bet % and money %:

- The popular free splits pages (Action Network, VSiN/DraftKings splits, Covers, ScoresAndOdds and others) are for viewing on the site. Action Network's terms explicitly prohibit page-scraping, robots and spiders. None of these sites offers a public API or a license for this data.
- Licensed commercial feeds exist, for example Sportradar's **NCAAFB Betting Splits** API, but they need a paid contract.

So nothing here scrapes. The UI, flags and briefing section are all built, and the feed is a setting:

- `SPLITS_PROVIDER=none` (default): cards show "feed not configured".
- `SPLITS_PROVIDER=file`: reads `data/splits_manual.json` (see `data/splits_manual.example.json`). Use it for numbers you're allowed to use.
- `SPLITS_PROVIDER=http_json`: GETs `SPLITS_URL` (with `Authorization: Bearer $SPLITS_API_KEY` if that's set). The response must use the normalized schema documented in `cfbmodel/splits.py`. Point it at a small adapter in front of a licensed vendor.

To add a vendor directly, subclass `SplitsProvider` in `cfbmodel/splits.py`, return normalized records from `fetch()`, and register it in `PROVIDERS`.

Flags: **Sharp $** means money % beats bet % on a side by 10+ points. **Public** means 65%+ of tickets. **Reverse line move** means the line moved 1+ point away from a side holding 55%+ of tickets. Set the thresholds with `CFB_SHARP_GAP` and `CFB_PUBLIC_BETS`.

## Local use

```bash
export CFBD_API_KEY=...
python -m cfbmodel refresh            # writes data/ and site/data/
python -m cfbmodel briefing --force --dry-run
python -m cfbmodel grade --no-recap
python -m http.server -d site 8000    # open http://localhost:8000
python -m unittest -v                 # offline tests (synthetic CFBD stand-in)
```

There are no dependencies beyond the Python 3.11+ standard library.

## Tuning

Set any of these as env vars in the workflows: `CFB_W_SP`, `CFB_W_FPI`, `CFB_W_ELO`, `CFB_HFA`, `CFB_LEAN_EDGE`, `CFB_PLAY_EDGE`, `CFB_STRONG_EDGE`, `CFB_LINE_PROVIDERS` (book priority for the current line), `CFB_TREND_SEASONS`, `CFB_BYE_DAYS`, `CFB_TREND_MIN_GAMES`, `CFB_SHARP_GAP`, `CFB_PUBLIC_BETS`, `CFB_CONF_HIGH`, `CFB_CONF_MEDIUM`, `CFB_TUNING`, `CFB_MIN_TUNE_GAMES`, `CFB_TUNE_PRIOR_GAMES`. Tuned weights in `data/model_weights.json` take precedence over `CFB_W_*` until you delete that file or turn tuning off. `cfbmodel/config.py` lists them all.

## Layout

```
cfbmodel/        pipeline (stdlib only)
  cfbd.py        CFBD API client
  ratings.py     SP+/FPI/Elo blend
  model.py       projections, picks, grading
  ats.py         historical ATS trends
  confidence.py  pick confidence score
  tuning.py      weekly SP+/FPI/Elo weight and home-field refit
  splits.py      bet-splits providers and divergence flags
  pipeline.py    refresh / grade / site data
  briefing.py    ntfy game-day briefing and recap
data/            committed state: picks log, line history, history cache
site/            static site (index.html, app.js, styles.css, data/*.json)
```

For entertainment and research only. Not betting advice.
