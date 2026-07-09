"""AWS Lambda entrypoint for the daily prediction job."""
import logging

from src.ml.predict import run_daily_predictions

logging.basicConfig(level=logging.INFO)


def handler(event, context):
    count = run_daily_predictions()
    return {"statusCode": 200, "body": {"predictions_written": count}}
