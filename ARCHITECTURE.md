# Football Prediction & Betting-Insight System — Architecture

A Python system that continuously ingests football (soccer) fixture data, trains an
XGBoost 1X2 (home win / draw / away win) model, and exposes an LLM chat layer ("what
are the best bets today?") on top of it.

## 1. High-level flow

```
                 ┌────────────────────┐
                 │  football-data.org  │  (fixtures, results, standings)
                 │  + Odds API         │  (Tipico-filtered 1X2 odds)
                 └─────────┬──────────┘
                            │  scheduled HTTPS calls (daily)
                            ▼
                 ┌────────────────────┐
                 │  Ingestion job      │  src/data_pipeline/*
                 │  (GitHub Actions,   │  - normalizes payloads
                 │   cron)             │  - upserts into Postgres
                 └─────────┬──────────┘
                            ▼
                 ┌────────────────────┐
                 │ Managed Postgres    │  teams, matches, odds, predictions
                 │ (Neon / RDS /       │
                 │  Cloud SQL)         │
                 └─────────┬──────────┘
              ┌────────────┼─────────────────┐
              ▼                              ▼
  ┌────────────────────┐          ┌────────────────────────┐
  │ Training job         │  daily  │ Prediction job          │  daily
  │ src/ml/train.py      │────────▶│ src/ml/predict.py        │
  │ → XGBoost model.json │  model  │ → 1X2 probs + EV vs odds │
  │ stored in models/    │ artifact│ → writes `predictions`   │
  └────────────────────┘          └───────────┬────────────┘
                                                ▼
                                   ┌────────────────────────┐
                                   │ LLM chat layer           │
                                   │ src/llm/assistant.py     │
                                   │ OpenAI + function-calling │
                                   │ tools that query Postgres │
                                   └───────────┬────────────┘
                                                ▼
                                     User: "Best value bets today?"
```

The LLM never sees raw scraped text and never "calculates" probabilities itself — it
calls typed tool functions that read the `predictions` and `odds` tables and hands the
LLM structured JSON. This keeps answers grounded in the model's actual numbers instead
of the LLM hallucinating stats, which matters a lot for anything bet-related.

## 2. Components

### 2.1 Data ingestion (`src/data_pipeline/`)
- `football_client.py` — client for [football-data.org's API](https://www.football-data.org/)
  (fixtures, results, standings), authenticated via a single `X-Auth-Token` header.
  Free tier: 10 requests/minute, current season included — API-Sports.io was tried
  first, but its free tier only allows the 2022-2024 seasons, rejecting any request for
  the current one. Tracks a fixed list of major leagues (`DEFAULT_LEAGUE_CODES`) rather
  than every league in existence, both to stay within the rate limit and because that's
  what the free tier covers anyway.
- `odds_client.py` — client for [The Odds API](https://the-odds-api.com), a documented,
  inexpensive odds source with a stable REST contract. Unlike tennis (which keys odds
  per-tournament, ephemeral to that event's dates), football sport keys are stable,
  season-long competitions (e.g. `soccer_epl`, `soccer_germany_bundesliga`), so
  `OddsAPIClient` just queries the fixed list directly instead of discovering
  "currently in season" keys first. `get_odds_for_bookmakers()` filters to specific
  bookmaker keys — by default `tipico_de`, so the odds recorded are Tipico's — via The
  Odds API's own `bookmakers` param, rather than scraping tipico.de directly. Football's
  `h2h` market returns three outcomes per event (home team, away team, and the literal
  string `"Draw"`), unlike tennis's two.
- `ingest.py` — orchestrates one ingestion cycle per configured league: pull upcoming
  fixtures → upsert `matches`; pull recent finished fixtures → backfill training data;
  pull standings → update each `teams` row's league position/points; pull Tipico 1X2
  odds (falling back to the broader eu/uk/us region odds if Tipico hasn't posted a line
  yet) for those matches → insert `odds` snapshots (append-only, so line movement over
  time is preserved).

### 2.2 Storage (`src/db/`)
Postgres via SQLAlchemy. Chosen over a NoSQL/BigQuery option because the domain is
relational (teams ↔ matches ↔ odds ↔ predictions) and small in volume (a handful of
major leagues' worth of fixtures per season) — a $0–25/mo serverless Postgres instance
is plenty.

Tables: `teams`, `matches`, `odds`, `predictions`, `sync_state`.

### 2.3 ML model (`src/ml/`)
- `features.py` — builds one feature row per finished match, from the home team's
  perspective (football has a real home/away asymmetry — home advantage is a genuine,
  learnable signal — unlike tennis, which had no home/away and so duplicated every
  match symmetrically to cancel out positional bias). Features: league position/points
  diff, recent form (points-per-game over the last 10 matches), recent goal-difference
  average, head-to-head points-per-game between the two teams, days since each team's
  last match (fatigue proxy).
- `train.py` — pulls finished matches from Postgres into pandas, trains an
  `XGBClassifier` (3-class: away win / draw / home win via `multi:softprob`), evaluates
  log-loss/accuracy on a held-out split, and saves the model + feature list to
  `models/model.json`.
- `predict.py` — for each upcoming match, builds features, gets
  `P(home) / P(draw) / P(away)` from the model, and combines each with the best
  available decimal odds for that outcome to compute expected value:
  `EV = prob * decimal_odds - 1`. The single best-EV outcome across all three is stored
  as the match's `value_pick`; matches whose best EV clears a threshold are flagged as
  "value bets" and written to `predictions`.

### 2.4 LLM chat layer (`src/llm/`)
- `tools.py` — plain Python functions (`get_upcoming_matches`, `get_match_prediction`,
  `get_best_value_bets`) that query Postgres and return JSON-serializable dicts. These
  are exposed to the OpenAI API as callable tools.
- `assistant.py` — a small function-calling loop: send the user's question + tool
  schemas to the model, execute whichever tools it asks for, feed results back, get a
  final natural-language answer. System prompt constrains it to only state facts backed
  by tool output and to always disclose the model's edge/EV numbers, not just a verdict.
- `cli.py` — a REPL so you can ask questions from a terminal.

### 2.5 Web layer (`src/web/`, `static/`)
- `app.py` — a FastAPI app exposing `/api/status`, `/api/matches`, `/api/value-bets`,
  `/api/chat`, and serving the `static/` frontend. Each read endpoint tries the real
  pipeline (Postgres + trained model) first; if that's unavailable (no `DATABASE_URL`
  configured, empty DB, etc.) it transparently falls back to `demo_data.py`'s simulated
  matches/Tipico-style odds so the UI is always explorable, with every response tagged
  `"demo": true/false` so the frontend can show a clear SIMULATION MODE badge instead of
  silently presenting fake numbers as real. `/api/chat` uses the OpenAI-backed
  `assistant.ask()` when `OPENAI_API_KEY` is set, else a small rule-based offline
  responder built on the same tool functions/demo data.
- `static/` — a single-page "JARVIS" HUD: dark holographic theme, an animated
  arc-reactor status indicator, per-match 1X2 win-probability/odds/EV cards, a scrolling
  value-bet ticker, and a chat panel with a typewriter effect and optional
  browser-native text-to-speech (`SpeechSynthesis`) so answers are "spoken" back,
  reminiscent of Iron Man's J.A.R.V.I.S. Plain HTML/CSS/JS — no build step.

### 2.6 Scheduling & deployment
This project runs on **GitHub Actions** (`.github/workflows/pipeline.yml`), not the AWS
Lambda setup `cloud/` describes below — a daily `schedule` trigger runs ingest → train
→ predict in sequence, and the same workflow can be dispatched manually for a single
step (`init-db`, `ingest`, `train-and-predict`, `predict-only`). The web UI itself is
deployed separately via `render.yaml` on [Render](https://render.com)'s free tier.

`cloud/` (Lambda handlers, `template.yaml` AWS SAM template) is an alternative,
more-scalable deployment path for higher-frequency ingestion than a single daily GitHub
Actions run — see below if you outgrow the GitHub Actions + Render setup.

## 3. Cloud provider recommendation

| Concern | AWS (recommended) | GCP alternative |
|---|---|---|
| Scheduled ingestion/prediction | Lambda (container image) + EventBridge Scheduler | Cloud Run Jobs + Cloud Scheduler |
| Database | RDS Postgres (or Aurora Serverless v2 for spiky load) — or skip AWS entirely and use **Neon**/**Supabase** serverless Postgres, which is cheaper for this workload | Cloud SQL Postgres |
| Model artifact storage | S3 | Cloud Storage |
| Secrets | Secrets Manager | Secret Manager |
| Chat interface hosting (optional) | Lambda + API Gateway, or just run `cli.py` locally | Cloud Functions |

Why AWS-first: Lambda's per-100ms billing suits bursty, infrequent jobs (a few
invocations/hour) far better than a always-on VM, and EventBridge Scheduler needs no
extra infra. If you don't want to manage RDS, use **Neon** (serverless Postgres,
generous free tier, scales to zero) as the DB regardless of which compute provider you
pick — it's the single biggest cost lever in this stack.

## 4. Cost considerations (rough, monthly, single-user scale)

| Item | Estimate | Notes |
|---|---|---|
| Football stats API (football-data.org) | $0–15 | Free tier: 10 requests/min, current season, covers this app's leagues — plenty for a daily ingest run; paid tiers only needed for higher-frequency polling or extra leagues |
| Odds API (The Odds API) | $0–59 | Free tier: 500 requests/mo; $59/mo tier covers frequent polling of a few sports |
| Postgres (Neon/Supabase free–pro) | $0–25 | Free tier is enough for solo use; paid tier removes cold-starts/sleep |
| Compute (GitHub Actions / Render free tier) | $0 | A daily scheduled run plus a low-traffic web service both fit comfortably in free-tier minutes |
| OpenAI API (gpt-4o-mini for chat) | $2–20 | Depends on chat volume; each Q&A is a handful of cheap tool-augmented calls |
| **Total** | **~$2–125/mo** | Free tiers alone cover light personal use entirely; costs only appear if you outgrow them |

Two things worth deciding up front because they drive cost the most:
1. **How real-time do you need odds?** Live in-play odds require frequent polling and
   push the odds-API tier up; pre-match odds fetched daily are much cheaper.
2. **Coverage** (how many leagues) directly drives both the stats-API and odds-API tier
   you need — broader coverage means more requests per ingestion run.

## 5. Notes & caveats

- Use official, documented APIs (as built here) rather than scraping bookmaker sites —
  scraping odds pages usually violates ToS and is fragile; the free/paid odds APIs
  exist specifically to be used programmatically.
- `football_client.py`'s response-shape parsing follows football-data.org's documented
  format rather than a live-tested payload, so `ingest.py` treats an unexpected shape
  the same as an unavailable source (skip + log) rather than crashing — check the logs
  after the first real ingestion run and adjust field names there if needed. This is
  also how the API-Sports.io attempt was caught: its free tier turned out to reject any
  request for the current season, discovered from exactly these logs.
- This system produces *statistical* win probabilities and EV estimates for
  informational purposes — it is not gambling advice, odds can move after predictions
  are computed, and past performance doesn't guarantee results. Bet responsibly and
  check local regulations on sports betting.
