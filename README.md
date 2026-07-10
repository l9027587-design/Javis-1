# Javis-1 — J.A.R.V.I.S. Tennis Prediction & Betting-Insight Assistant

A Python system that ingests tennis data on a schedule, trains an XGBoost
win-probability model, prices it against **Tipico's odds**, and exposes both a
terminal chat and a futuristic **JARVIS-style holographic web UI** where you can
ask *"what are the most likely bets with the best odds today?"*.

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the full design, cloud deployment
options, and cost breakdown.

## Project layout

```
src/
  config.py              settings from environment variables
  db/                     SQLAlchemy models + session (Postgres)
  data_pipeline/
    api_client.py         tennis stats API client (Sportradar / RapidAPI)
    odds_client.py         odds client (the-odds-api.com), incl. Tipico-filtered fetch
    ingest.py               orchestrates one fetch-and-store cycle
  ml/
    features.py            feature engineering from stored matches
    train.py                trains + saves the XGBoost model
    predict.py               scores upcoming matches, computes EV vs Tipico odds
  llm/
    tools.py                 DB-backed functions exposed to the LLM
    assistant.py               OpenAI function-calling chat loop
  web/
    app.py                     FastAPI backend for the JARVIS HUD (+ demo fallback)
    demo_data.py                 simulated matches/odds so the UI works with no keys set
  cli.py                      terminal chat REPL
static/                        JARVIS-style holographic web frontend (HTML/CSS/JS)
cloud/                        Lambda handlers, Dockerfile, AWS SAM template
scripts/                       one-off/local helper scripts
```

## The JARVIS web UI

```bash
pip install -r requirements.txt
uvicorn src.web.app:app --reload --port 8000
# open http://localhost:8000
```

A dark, holographic HUD (à la Iron Man's J.A.R.V.I.S.): an arc-reactor status
indicator, live match cards, an animated win-probability/odds/EV readout per match,
a scrolling value-bet ticker, and a chat panel that answers in a JARVIS voice
(with optional text-to-speech via the browser's Speech Synthesis API).

It works with **zero configuration** — if Postgres/`OPENAI_API_KEY` aren't set up
yet, it automatically falls back to `src/web/demo_data.py`'s simulated matches/odds
so you can see the whole interface immediately (clearly labeled "SIMULATION MODE").
Once `DATABASE_URL`, a tennis-stats API key, `ODDS_API_KEY`, and `OPENAI_API_KEY`
are configured and the pipeline has run, it automatically switches to live data.

### Tipico as the odds source

Odds are sourced from **[The Odds API](https://the-odds-api.com)**, which carries
Tipico (`tipico_de`) as one of its licensed bookmaker feeds — set via
`ODDS_BOOKMAKERS=tipico_de` in `.env` (the default). This project deliberately does
**not** scrape tipico.de directly: scraping a bookmaker's own site usually violates
its terms of service and is fragile, whereas The Odds API is a documented, ToS-compliant
aggregator that includes Tipico's prices. If Tipico hasn't posted a line for a given
match yet, ingestion falls back to the broader eu/uk/us region odds.

## Quickstart (local)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in TENNIS_API_KEY, ODDS_API_KEY, DATABASE_URL, OPENAI_API_KEY

python -m scripts.init_db          # create tables
python -m src.data_pipeline.ingest # first data pull (rankings, schedule, odds)
python -m src.ml.train             # train once you have finished matches in the DB
python -m src.ml.predict           # score upcoming matches
python -m src.cli                  # chat: "what are the best value bets today?"

uvicorn src.web.app:app --reload --port 8000   # or: the JARVIS web UI, see below
```

Before this will produce real predictions you need: (1) an active tennis-stats API
key (Sportradar or a RapidAPI tennis product) with the endpoint paths in
`src/data_pipeline/api_client.py` matched to your subscription, (2) an
[the-odds-api.com](https://the-odds-api.com) key for market odds, (3) enough
ingested finished matches for `train.py` to fit a model on, and (4) an OpenAI API key.

## Deploying to the cloud

`cloud/template.yaml` is an AWS SAM template that runs ingestion every 30 minutes,
predictions daily, and retraining weekly as container-image Lambdas on EventBridge
schedules. Build and push the image, store your API keys in Secrets Manager, then:

```bash
sam build && sam deploy --guided
```

See ARCHITECTURE.md §3–4 for the AWS vs. GCP tradeoffs and a monthly cost estimate.
