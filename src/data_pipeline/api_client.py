"""Generic client for a tennis stats provider (Sportradar or a RapidAPI tennis product).

Endpoint paths differ by provider and subscription tier, so they are kept in
`ENDPOINTS` below rather than hardcoded through the client — fill them in from your
provider's docs after you subscribe. The two supported auth styles (Sportradar's
query-param API key, RapidAPI's header-based key) are handled in `_headers`/`_params`.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests
from tenacity import retry, retry_if_exception, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import settings

logger = logging.getLogger(__name__)


def _is_rate_limit_error(exc: BaseException) -> bool:
    """RapidAPI's Basic/free tiers enforce a per-second rate limit and report it as a
    plain 403 (not 429) — retry those with backoff instead of failing the whole run."""
    return isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code in (403, 429)


# Minimum gap between requests to a single provider, to stay under RapidAPI Basic-tier
# per-second rate limits (which the ingest loop would otherwise hit on its ~8 calls/run).
MIN_REQUEST_INTERVAL_SECONDS = 1.1

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
        self._last_request_at: float = 0.0

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
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout))
        | retry_if_exception(_is_rate_limit_error),
    )
    def _get(self, path: str, **path_params: str) -> Any:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
            time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
        self._last_request_at = time.monotonic()

        url = self.base_url + path.format(**path_params)
        response = self.session.get(url, headers=self._headers(), params=self._params(), timeout=15)
        if not response.ok:
            # RapidAPI's error body (e.g. "not subscribed", "quota exceeded", "rate
            # limit") is otherwise swallowed by raise_for_status()'s generic message.
            logger.warning(
                "%s returned HTTP %d for %s: %s", self.provider, response.status_code, url, response.text[:500]
            )
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
