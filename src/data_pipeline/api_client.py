"""Generic client for a tennis stats provider (Sportradar or a RapidAPI tennis product).

Endpoint paths differ by provider and subscription tier, so they are kept in
`ENDPOINTS` below rather than hardcoded through the client — fill them in from your
provider's docs after you subscribe. The two supported auth styles (Sportradar's
query-param API key, RapidAPI's header-based key) are handled in `_headers`/`_params`.
"""
from __future__ import annotations

import logging
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import settings

logger = logging.getLogger(__name__)

# Fill these in to match your subscribed product's actual paths.
ENDPOINTS = {
    "sportradar": {
        "base_url": "https://api.sportradar.com/tennis/trial/v3/en",
        "rankings": "/rankings.json",
        "schedule": "/schedules/{date}/schedule.json",
        "player_profile": "/competitors/{player_id}/profile.json",
        "match_summary": "/matches/{match_id}/summary.json",
    },
    "rapidapi": {
        # "Tennis API - ATP WTA ITF" (matchstat.com), docs: tennisapidoc.matchstat.com
        "base_url": "https://tennis-api-atp-wta-itf.p.rapidapi.com",
        "rankings": "/tennis/v2/{tour}/ranking/singles",
        "schedule": "/tennis/v2/{tour}/fixtures/{date}",
        "player_profile": "/tennis/v2/{tour}/player/profile/{player_id}",
        # No documented single-match-by-id endpoint on this provider; unused by the
        # ingest pipeline today (fixtures already carry match data). Placeholder so a
        # future caller gets a 404 to investigate rather than a str.format() crash.
        "match_summary": "/tennis/v2/{tour}/fixtures/match/{match_id}",
    },
}


class TennisAPIClient:
    def __init__(self, provider: str | None = None) -> None:
        self.provider = provider or settings.tennis_api_provider
        if self.provider not in ENDPOINTS:
            raise ValueError(f"Unknown provider '{self.provider}', expected one of {list(ENDPOINTS)}")
        self.endpoints = ENDPOINTS[self.provider]
        self.base_url = self.endpoints["base_url"]
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        if self.provider == "rapidapi":
            return {
                "X-RapidAPI-Key": settings.tennis_api_key,
                "X-RapidAPI-Host": settings.tennis_api_host,
            }
        return {}

    def _params(self) -> dict[str, str]:
        if self.provider == "sportradar":
            return {"api_key": settings.tennis_api_key}
        return {}

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    )
    def _get(self, path: str, **path_params: str) -> Any:
        url = self.base_url + path.format(**path_params)
        response = self.session.get(url, headers=self._headers(), params=self._params(), timeout=15)
        if response.status_code == 429:
            logger.warning("Rate limited by %s, backing off", self.provider)
            response.raise_for_status()
        response.raise_for_status()
        return response.json()

    def get_rankings(self, tour: str = "atp") -> Any:
        """Current ATP or WTA singles rankings list. tour: 'atp' or 'wta'."""
        return self._get(self.endpoints["rankings"], tour=tour)

    def get_schedule(self, date: str, tour: str = "atp") -> Any:
        """Matches scheduled for a given date (YYYY-MM-DD) and tour ('atp' or 'wta')."""
        return self._get(self.endpoints["schedule"], tour=tour, date=date)

    def get_player_profile(self, player_id: str, tour: str = "atp") -> Any:
        return self._get(self.endpoints["player_profile"], tour=tour, player_id=player_id)

    def get_match_summary(self, match_id: str, tour: str = "atp") -> Any:
        return self._get(self.endpoints["match_summary"], tour=tour, match_id=match_id)
