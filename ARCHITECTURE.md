# Tennis Prediction & Betting-Insight System — Architecture

A Python system that continuously ingests tennis data, trains an XGBoost win-probability
model, and exposes an LLM chat layer ("what are the best bets today?") on top of it.

## 1. High-level flow

```
                 ┌────────────────────┐
                 │  Tennis data APIs  │  (Sportradar / RapidAPI: rankings,
                 │  + Odds API        │   schedule, match stats, H2H, odds)
                 └─────────┬──────────┘
                            │  scheduled HTTPS calls (every 15–60 min)
                            ▼
                 ┌────────────────────┐
                 │  Ingestion job      │  src/data_pipeline/*
                 │  (Lambda / Cloud    │  - normalizes payloads
                 │   Function, cron)   │  - upserts into Postgres
                 └─────────┬──────────┘
                            ▼
                 ┌────────────────────┐
                 │ Managed Postgres    │  players, rankings_history, matches,
                 │ (Neon / RDS /       │  match_stats, odds, predictions
                 │  Cloud SQL)         │
                 └─────────┬──────────┘
              ┌────────────┼─────────────────┐
              ▼                              ▼
  ┌────────────────────┐          ┌────────────────────────┐
  │ Training job         │  weekly │ Prediction job          │  daily/hourly
  │ src/ml/train.py      │────────▶│ src/ml/predict.py        │
  │ → XGBoost model.json │  model  │ → win-prob + EV vs odds  │
  │ stored in S3/GCS      │ artifact│ → writes `predictions`   │
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
- `api_client.py` — generic, retrying HTTP client for the tennis stats provider
  (Sportradar Tennis API or a RapidAPI tennis provider). Endpoint paths are kept in a
  config dict because they differ by provider/plan — point it at whichever API you
  subscribe to.
- `odds_client.py` — client for [The Odds API](https://the-odds-api.com) (`tennis_atp` /
  `tennis_wta` sports keys), a documented, inexpensive odds source with a stable REST
  contract. Good complement to Sportradar/RapidAPI, which often don't include live
  bookmaker odds on cheaper tiers.
- `ingest.py` — orchestrates one ingestion cycle: pull rankings → upsert `players`;
  pull upcoming schedule → upsert `matches`; pull odds for those matches → insert
  `odds` snapshots (append-only, so line movement over time is preserved).

### 2.2 Storage (`src/db/`)
Postgres via SQLAlchemy. Chosen over a NoSQL/BigQuery option because the domain is
relational (players ↔ matches ↔ odds ↔ predictions) and small in volume (tens of
thousands of matches/year) — a $0–25/mo serverless Postgres instance is plenty.

Tables: `players`, `ranking_history`, `matches`, `match_stats`, `odds`, `predictions`.

### 2.3 ML model (`src/ml/`)
- `features.py` — builds a symmetric feature vector per match from two perspectives
  (player A vs B, and B vs A, each a separate training row) so the model doesn't learn
  a "player 1 always wins" artifact. Features: ranking/points diff, recent form
  (win-rate over last 10 matches), surface win-rate diff, H2H win-rate, days since last
  match (fatigue proxy).
- `train.py` — pulls finished matches from Postgres into pandas, trains an
  `XGBClassifier` (binary: does player A win), evaluates log-loss/AUC on a held-out
  time split (never shuffle chronological sports data), and saves the model + feature
  list to `models/model.json` (and optionally uploads to S3/GCS).
- `predict.py` — for each upcoming match, builds features, gets `P(A wins)` from the
  model, and combines it with the best available decimal odds to compute expected
  value: `EV = prob * decimal_odds - 1`. Matches with positive EV above a threshold are
  flagged as "value bets" and written to `predictions`.

### 2.4 LLM chat layer (`src/llm/`)
- `tools.py` — plain Python functions (`get_upcoming_matches`, `get_match_prediction`,
  `get_best_value_bets`) that query Postgres and return JSON-serializable dicts. These
  are exposed to the OpenAI API as callable tools.
- `assistant.py` — a small function-calling loop: send the user's question + tool
  schemas to the model, execute whichever tools it asks for, feed results back, get a
  final natural-language answer. System prompt constrains it to only state facts backed
  by tool output and to always disclose the model's edge/EV numbers, not just a verdict.
- `cli.py` — a REPL so you can ask questions from a terminal; the same `assistant.py`
  function is trivially wrapped in a FastAPI endpoint or Slack/Telegram bot later.

### 2.5 Scheduling & deployment (`cloud/`)
Two independent scheduled jobs, deployed as containers so `xgboost`/`pandas` fit
comfortably (they exceed the plain zip Lambda size limit):
- **Ingestion**: every 15–60 min (more often close to match time for odds).
- **Prediction**: once daily, plus re-run a few hours before big matches.
- **Training**: weekly, or triggered manually after ingesting a big batch of results.

`cloud/template.yaml` is an AWS SAM template wiring these up as container-image Lambdas
on EventBridge schedules, reading secrets (API keys, DB URL) from AWS Secrets Manager.

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
| Tennis stats API | $0–150+ | RapidAPI tennis providers have freemium tiers ($0–25/mo) with limited calls/day; Sportradar is enterprise-priced (often $500+/mo, quote-based) — start on RapidAPI/API-Tennis and only upgrade if coverage/latency is insufficient |
| Odds API (The Odds API) | $0–59 | Free tier: 500 requests/mo; $59/mo tier covers frequent polling of a few sports |
| Postgres (Neon/Supabase free–pro) | $0–25 | Free tier is enough for solo use; paid tier removes cold-starts/sleep |
| Compute (Lambda/Cloud Run, low volume) | ~$0–5 | A few thousand short invocations/month is within free tiers |
| S3/Cloud Storage | <$1 | Tiny model artifacts |
| OpenAI API (gpt-4o-mini for chat) | $2–20 | Depends on chat volume; each Q&A is a handful of cheap tool-augmented calls |
| **Total** | **~$5–260/mo** | Dominated entirely by which stats-API tier you need; the ML+LLM+hosting portion is cheap |

Two things worth deciding up front because they drive cost the most:
1. **How real-time do you need odds?** Live in-play odds require frequent polling and
   push the odds-API tier up; pre-match odds fetched hourly are much cheaper.
2. **Coverage** (ATP/WTA only vs. Challengers/ITF) directly drives which stats-API tier
   you need — broader coverage means a pricier plan.

## 5. Notes & caveats

- Use official, documented APIs (as built here) rather than scraping bookmaker sites —
  scraping odds pages usually violates ToS and is fragile; the free/paid odds APIs
  exist specifically to be used programmatically.
- Sportradar/RapidAPI endpoint paths vary by product and subscription tier — the ones
  in `api_client.py` are placeholders to be filled in from your provider's docs after
  you subscribe (`ENDPOINTS` dict at the top of the file).
- This system produces *statistical* win probabilities and EV estimates for
  informational purposes — it is not gambling advice, odds can move after predictions
  are computed, and past performance doesn't guarantee results. Bet responsibly and
  check local regulations on sports betting.
