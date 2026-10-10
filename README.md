# CFB Model

A static college football model site on GitHub Pages, driven by
[CollegeFootballData.com](https://collegefootballdata.com) and GitHub Actions.
The visual design follows [davidsasser.com/cfb](https://davidsasser.com/cfb): warm paper background, square ruled cards, a deep-green accent, and Geist / Geist Mono type.

- **Power ratings**: SP+, ESPN FPI and CFBD Elo are rescaled onto the SP+ points scale and blended (40/35/25 by default).
- **Projections**: margin = rating diff + home field (2.5, 0 at neutral sites). Totals come from SP+ offense/defense. Output is a projected score, model spread and win probability.
- **Picks**: an ATS side against the current line for every game, plus the edge (expected cover margin) and a tier: lean ≥ 2, play ≥ 4, strong ≥ 7. Every game also gets an SU pick. Picks lock at kickoff and are graded SU and ATS.
- **Confidence**: a 0–100 score per pick, separate from edge. It combines how many of SP+/FPI/Elo cover on their own and how tightly they agree, the edge, line movement and sharp flags, situational ATS trends, and data quality. High is 70+ and Medium is 55+. A small-edge pick can still be High, and game-day briefings include High-confidence sides even when the edge is small. The Record tab tracks ATS results by confidence level.
- **Weekly tuning**: every pick stores each source's projected margin. After Sunday grading, the model fits the SP+/FPI/Elo weights and home field that best predict this season's actual margins, then moves part of the way there: alpha = n/(n+250), so it moves more as games accumulate. Tuning starts after 40 graded games. The current weights and their history are saved in `data/model_weights.json`, shown on the Method tab and included in the Sunday recap. Set `CFB_TUNING=off` to freeze the weights.
- **ATS trends**: closing lines and results for the last 6 seasons plus the current one. Each team's ATS record is split overall, as favorite or underdog, home/away/neutral (and combined, e.g. road dog), in conference play, off a bye (13+ days) and over the last 10 games. Each game card shows the splits that apply to that game.
- **Bet splits**: public bet % and money % per side, with sharp/public/reverse-line-move flags. The feed is swappable (see below).
- **ntfy briefing**: sent at 9 AM CT on game days with picks, biggest edges, line moves, key ATS trends and bet splits. A recap goes out after Sunday grading.

## Setup

1. **Secrets** (Settings → Secrets and variables → Actions → *Secrets*):
   - `CFBD_API_KEY` (required): your CollegeFootballData key.
   - `NTFY_TOPIC` (required for push): your ntfy topic name. Pick a hard-to-guess one, because anyone who knows a public ntfy.sh topic can read it.
   - `NTFY_TOKEN` (optional): an access token if your topic is protected.
   - `SPLITS_API_KEY` (optional): a bearer token for an `http_json` splits feed.
2. **Variables** (optional, *Variables* tab): `SITE_URL`, `NTFY_SERVER`, `SPLITS_PROVIDER` (`none` | `file` | `http_json`), `SPLITS_URL`.
3. **Pages**: Settings → Pages → Source: **GitHub Actions**.
4. **Default branch**: scheduled workflows only run on the default branch, and the `github-pages` environment only deploys from it by default. Merge this work into `main` and make `main` the default branch.
5. Run **Daily refresh** once from the Actions tab. The first run backfills 6 seasons of games and lines (about 18 CFBD calls) into `data/cache/`. Later runs use about 8–10 calls a day.

## Schedules

| Workflow | When | What |
|---|---|---|
| `refresh.yml` | daily 11:00 UTC | ratings, lines, projections, picks (re-priced until kickoff), trends, splits, grading → commit → deploy |
| `grade.yml` | Sundays 12:00 UTC | grades SU and ATS for every finished pick, rebuilds the record, sends an ntfy recap |
| `briefing.yml` | 14:07 and 15:07 UTC | sends the ntfy briefing once per game day, between 9 and 11 AM CT (covers CDT/CST and late cron starts) |
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
