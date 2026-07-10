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


def _clean(value: str) -> str:
    """Strip whitespace/newlines and wrapping quotes that mobile copy-paste into a
    GitHub secret tends to carry over — invisible in the UI but invalid as an HTTP
    header value or breaking exact-match comparisons."""
    return value.strip().strip("'\"")


def _normalize_database_url(url: str) -> str:
    """Accept a plain 'postgresql://...' URL (e.g. copied straight from a hosting
    provider's dashboard) and rewrite it to use the psycopg2 driver SQLAlchemy needs."""
    url = _clean(url)
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://") :]
    return url


@dataclass(frozen=True)
class Settings:
    # `os.getenv(name) or default` (not the two-arg form) is deliberate: GitHub Actions
    # sets a referenced-but-undefined `vars.X` to an empty string rather than leaving it
    # unset, which would silently defeat a `os.getenv(name, default)` fallback.
    tennis_api_provider: str = _clean(os.getenv("TENNIS_API_PROVIDER") or "rapidapi")
    tennis_api_key: str = _clean(os.getenv("TENNIS_API_KEY", ""))
    tennis_api_host: str = _clean(os.getenv("TENNIS_API_HOST", ""))

    odds_api_key: str = _clean(os.getenv("ODDS_API_KEY", ""))
    # Bookmaker key(s) on The Odds API to pull odds from, comma-separated.
    # "tipico_de" is Tipico's feed on The Odds API — a documented, ToS-compliant
    # source (we never scrape tipico.de directly). Leave empty to fall back to
    # the broader eu/uk/us region odds instead of one specific bookmaker.
    odds_bookmakers: str = _clean(os.getenv("ODDS_BOOKMAKERS") or "tipico_de")

    database_url: str = _normalize_database_url(
        os.getenv("DATABASE_URL") or "postgresql+psycopg2://user:password@localhost:5432/tennis"
    )

    openai_api_key: str = _clean(os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = _clean(os.getenv("OPENAI_MODEL") or "gpt-4o-mini")

    model_s3_bucket: str = _clean(os.getenv("MODEL_S3_BUCKET", ""))
    aws_region: str = _clean(os.getenv("AWS_REGION") or "us-east-1")


settings = Settings()
