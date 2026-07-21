"""Central configuration loaded from environment variables (.env in local dev)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _clean(value: str) -> str:
    """Strip wrapping quotes and *all* whitespace/newlines, including in the middle
    of the value — mobile copy-paste out of a wrapped code block can splice a
    newline into the middle of a token, not just at the ends, which is invisible in
    the GitHub secrets UI but invalid as an HTTP header value."""
    value = value.strip().strip("'\"")
    return re.sub(r"\s+", "", value)


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
    # football-data.org's Football API (https://www.football-data.org/) -- fixtures,
    # results, and standings. Auth is a single header, no separate host/provider needed.
    # (API-Sports.io was tried first, but its free tier only allows the 2022-2024
    # seasons -- rejects any request for the current one.)
    football_api_key: str = _clean(os.getenv("FOOTBALL_API_KEY", ""))

    odds_api_key: str = _clean(os.getenv("ODDS_API_KEY", ""))
    # Bookmaker key(s) on The Odds API to pull odds from, comma-separated.
    # "tipico_de" is Tipico's feed on The Odds API — a documented, ToS-compliant
    # source (we never scrape tipico.de directly). Leave empty to fall back to
    # the broader eu/uk/us region odds instead of one specific bookmaker.
    odds_bookmakers: str = _clean(os.getenv("ODDS_BOOKMAKERS") or "tipico_de")

    database_url: str = _normalize_database_url(
        os.getenv("DATABASE_URL") or "postgresql+psycopg2://user:password@localhost:5432/football"
    )

    openai_api_key: str = _clean(os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = _clean(os.getenv("OPENAI_MODEL") or "gpt-4o-mini")

    model_s3_bucket: str = _clean(os.getenv("MODEL_S3_BUCKET", ""))
    aws_region: str = _clean(os.getenv("AWS_REGION") or "us-east-1")


settings = Settings()
