"""Central configuration loaded from environment variables (.env in local dev)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    tennis_api_provider: str = os.getenv("TENNIS_API_PROVIDER", "rapidapi")
    tennis_api_key: str = os.getenv("TENNIS_API_KEY", "")
    tennis_api_host: str = os.getenv("TENNIS_API_HOST", "")

    odds_api_key: str = os.getenv("ODDS_API_KEY", "")

    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql+psycopg2://user:password@localhost:5432/tennis"
    )

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    model_s3_bucket: str = os.getenv("MODEL_S3_BUCKET", "")
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")


settings = Settings()
