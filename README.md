# Javis-1 — Tennis Prediction & Betting-Insight Assistant

A Python system that ingests tennis data on a schedule, trains an XGBoost
win-probability model, and lets you ask an LLM chat questions like *"what are the
most likely bets with the best odds today?"*.

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the full design, cloud deployment
options, and cost breakdown.

## Project layout

```
src/
  config.py              settings from environment variables
  db/                     SQLAlchemy models + session (Postgres)
  data_pipeline/
    api_client.py         tennis stats API client (Sportradar / RapidAPI)
    odds_client.py         odds client (the-odds-api.com)
    ingest.py               orchestrates one fetch-and-store cycle
  ml/
    features.py            feature engineering from stored matches
    train.py                trains + saves the XGBoost model
    predict.py               scores upcoming matches, computes EV vs odds
  llm/
    tools.py                 DB-backed functions exposed to the LLM
    assistant.py               OpenAI function-calling chat loop
  cli.py                      terminal chat REPL
cloud/                        Lambda handlers, Dockerfile, AWS SAM template
scripts/                       one-off/local helper scripts
```

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
