"""Client for BALLDONTLIE's ATP/WTA tennis API (https://www.balldontlie.io/), used for the
live upcoming schedule -- the metered stats API configured via TENNIS_API_KEY has been unable
to provide this reliably (persistent daily quota exhaustion on a RapidAPI Basic-tier plan).

Free tier, requires an API key (BALLDONTLIE_API_KEY) from a free account -- see .env.example.
Endpoint paths and parameter names come from BALLDONTLIE's own docs
(https://www.balldontlie.io/openapi.yml, https://atp.balldontlie.io/, https://wta.balldontlie.io/)
rather than a live test against a real key, so the exact response schema is unverified; ingest.py
treats parsing failures the same as an unavailable source (skip and log) rather than crashing.
"""
from __future__ import annotations

from typing import Any

import requests

from src.config import settings

BASE_URL = "https://api.balldontlie.io"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.balldontlie_api_key}"}


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(f"{BASE_URL}{path}", headers=_headers(), params=params or {}, timeout=15)
    if not response.ok:
        raise requests.HTTPError(
            f"{response.status_code} for {response.url}: {response.text[:300]}", response=response
        )
    data = response.json()
    return data.get("data", data) if isinstance(data, dict) else data


def get_matches(tour: str, season: int, **params: Any) -> list[dict]:
    """Matches for one tour ('atp' or 'wta') and season (year)."""
    return _get(f"/{tour}/v1/matches", params={"season": season, **params})


def get_rankings(tour: str, **params: Any) -> list[dict]:
    """Current rankings for one tour ('atp' or 'wta')."""
    return _get(f"/{tour}/v1/rankings", params=params)
