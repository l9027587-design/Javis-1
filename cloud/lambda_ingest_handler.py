"""AWS Lambda entrypoint for the ingestion job (bind to an EventBridge schedule)."""
import logging

from src.data_pipeline.ingest import run_ingestion

logging.basicConfig(level=logging.INFO)


def handler(event, context):
    result = run_ingestion()
    return {"statusCode": 200, "body": result}
