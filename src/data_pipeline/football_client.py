"""Client for API-Sports.io's Football API (https://api-sports.io/sports/football),
used for the fixtures schedule, results backfill, and league standings -- replaces the
tennis stats provider entirely now that the app covers football instead of tennis.

Free tier: 100 requests/day, every endpoint included, single API key
(https://www.api-football.com/ -- "HOW TO GET STARTED" guide). Auth is one header
(x-apisports-key), not a query-param key like some RapidAPI-fronted providers use.

Endpoint shapes below follow API-Football v3's documented/widely-tutorialized response
format (https://api-sports.io/documentation/football/v3) rather than a live test against
a real key, so exact field names are a best-effort match -- ingest.py parses defensively
(skip + log on an unexpected shape) the same way it already does for other sources.
"""
from __future__ import annotations

from typing import Any

import requests

from src.config import settings

BASE_URL = "https://v3.football.api-sports.io"

# A handful of major leagues, to stay well within the 100-req/day free tier -- each
# ingest run costs ~2 calls per league (upcoming fixtures + recent results), so this
# list directly controls the daily request budget. IDs are API-Sports.io's own,
# documented via its Leagues endpoint.
DEFAULT_LEAGUE_IDS: dict[int, str] = {
    39: "Premier League",
    140: "La Liga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
    2: "UEFA Champions League",
}


def _headers() -> dict[str, str]:
    return {"x-apisports-key": settings.football_api_key}


def _get(path: str, params: dict[str, Any] | None = None) -> list[dict]:
    response = requests.get(f"{BASE_URL}{path}", headers=_headers(), params=params or {}, timeout=15)
    if not response.ok:
        raise requests.HTTPError(
            f"{response.status_code} for {response.url}: {response.text[:300]}", response=response
        )
    data = response.json()
    errors = data.get("errors")
    if errors:
        # api-sports.io returns HTTP 200 with a populated "errors" object for things
        # like an invalid key or an exhausted daily quota, rather than a 4xx -- surface
        # it as a real exception instead of silently returning an empty response.
        raise requests.HTTPError(f"api-sports.io error for {response.url}: {errors}")
    return data.get("response", [])


def get_upcoming_fixtures(league_id: int, season: int, count: int = 10) -> list[dict]:
    """Next `count` scheduled fixtures for one league/season."""
    return _get("/fixtures", params={"league": league_id, "season": season, "next": count})


def get_recent_results(league_id: int, season: int, count: int = 50) -> list[dict]:
    """Last `count` finished fixtures for one league/season -- used to backfill training data."""
    return _get("/fixtures", params={"league": league_id, "season": season, "last": count})


def get_standings(league_id: int, season: int) -> list[dict]:
    """Current league table for one league/season."""
    return _get("/standings", params={"league": league_id, "season": season})
