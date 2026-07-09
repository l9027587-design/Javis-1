"""Run ingest -> (assumes a model already exists) predict, once, locally.

    python -m scripts.run_pipeline_once
"""
import logging

from src.data_pipeline.ingest import run_ingestion
from src.ml.predict import run_daily_predictions

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    print("Ingesting...", run_ingestion())
    print("Predicting...", run_daily_predictions())
