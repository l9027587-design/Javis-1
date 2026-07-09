"""AWS Lambda entrypoint for the weekly (re)training job."""
import logging

from src.ml.train import train

logging.basicConfig(level=logging.INFO)


def handler(event, context):
    metrics = train()
    return {"statusCode": 200, "body": metrics}
