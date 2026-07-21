"""Client for football-data.org's Football API (https://www.football-data.org/), used
for the fixtures schedule, results backfill, and league standings.

Free tier: 10 requests/minute, current-season data included -- unlike API-Sports.io's
free tier (tried first), which turned out to only allow the 2022-2024 seasons and
rejects any request for the current one. Auth is a single header (X-Auth-Token).

Endpoint shapes follow football-data.org's documented v4 API
(https://docs.football-data.org/general/v4/) rather than a live test against a real
key, so exact field names are a best-effort match -- ingest.py parses defensively
(skip + log on an unexpected shape) the same way it already does for other sources.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import requests

from src.config import settings

BASE_URL = "https://api.football-data.org/v4"

# football-data.org's own competition codes -- covers the same leagues this app
# tracked under API-Sports.io's numeric league IDs.
DEFAULT_LEAGUE_CODES: dict[str, str] = {
    "PL": "Premier League",
    "PD": "La Liga",
    "BL1": "Bundesliga",
    "SA": "Serie A",
    "FL1": "Ligue 1",
    "CL": "UEFA Champions League",
}


def _headers() -> dict[str, str]:
    return {"X-Auth-Token": settings.football_api_key}


def _get(path: str, params: dict[str, Any] | None = None) -> dict:
    response = requests.get(f"{BASE_URL}{path}", headers=_headers(), params=params or {}, timeout=15)
    if not response.ok:
        raise requests.HTTPError(
            f"{response.status_code} for {response.url}: {response.text[:300]}", response=response
        )
    return response.json()


def get_upcoming_fixtures(competition_code: str, days_ahead: int = 10) -> list[dict]:
    """Scheduled matches for one competition within the next `days_ahead` days."""
    today = dt.date.today()
    data = _get(
        f"/competitions/{competition_code}/matches",
        params={
            "dateFrom": today.isoformat(),
            "dateTo": (today + dt.timedelta(days=days_ahead)).isoformat(),
            "status": "SCHEDULED",
        },
    )
    return data.get("matches", [])


def get_recent_results(competition_code: str, days_back: int = 30) -> list[dict]:
    """Finished matches for one competition within the last `days_back` days."""
    today = dt.date.today()
    data = _get(
        f"/competitions/{competition_code}/matches",
        params={
            "dateFrom": (today - dt.timedelta(days=days_back)).isoformat(),
            "dateTo": today.isoformat(),
            "status": "FINISHED",
        },
    )
    return data.get("matches", [])


def get_standings(competition_code: str) -> list[dict]:
    """Current league table for one competition (overall/TOTAL standings)."""
    data = _get(f"/competitions/{competition_code}/standings")
    for block in data.get("standings", []):
        if block.get("type") == "TOTAL":
            return block.get("table", [])
    return []
