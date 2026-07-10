"""Client for The Odds API (https://the-odds-api.com) — h2h (moneyline) tennis odds."""
from __future__ import annotations

from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings

BASE_URL = "https://api.the-odds-api.com/v4"


class OddsAPIClient:
    def __init__(self, tour_prefix: str = "tennis_atp") -> None:
        """tour_prefix: 'tennis_atp' or 'tennis_wta'.

        The Odds API has no single blanket sport_key covering "all ATP" or "all WTA"
        matches at once — tennis is keyed per tournament (e.g. 'tennis_atp_wimbledon',
        'tennis_atp_french_open'), and a given key only exists while that tournament is
        actually in season. So every call first lists currently in-season sports and
        queries each tournament key that starts with this prefix.
        """
        self.tour_prefix = tour_prefix
        self.session = requests.Session()

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _active_tournament_keys(self) -> list[str]:
        """Sport keys for currently in-season tournaments matching self.tour_prefix."""
        response = self.session.get(
            f"{BASE_URL}/sports", params={"apiKey": settings.odds_api_key}, timeout=15
        )
        response.raise_for_status()
        return [s["key"] for s in response.json() if s.get("key", "").startswith(self.tour_prefix)]

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _get_odds_for_sport(self, sport_key: str, params: dict[str, str]) -> list[dict[str, Any]]:
        url = f"{BASE_URL}/sports/{sport_key}/odds"
        response = self.session.get(
            url, params={**params, "apiKey": settings.odds_api_key}, timeout=15
        )
        response.raise_for_status()
        return response.json()

    def get_odds(self, regions: str = "eu,uk,us", markets: str = "h2h") -> list[dict[str, Any]]:
        """Returns bookmaker h2h (moneyline) odds for every in-season tournament in this tour."""
        params = {"regions": regions, "markets": markets, "oddsFormat": "decimal"}
        events: list[dict[str, Any]] = []
        for sport_key in self._active_tournament_keys():
            events.extend(self._get_odds_for_sport(sport_key, params))
        return events

    def get_odds_for_bookmakers(self, bookmakers: str, markets: str = "h2h") -> list[dict[str, Any]]:
        """Same as get_odds(), filtered to specific bookmaker keys (e.g. 'tipico_de').

        The Odds API carries Tipico as a licensed bookmaker feed, so this is the
        ToS-compliant way to get "Tipico odds" — we never scrape tipico.de directly,
        which would violate their terms of service and is explicitly the kind of thing
        this project avoids (see ARCHITECTURE.md).
        """
        params = {"bookmakers": bookmakers, "markets": markets, "oddsFormat": "decimal"}
        events: list[dict[str, Any]] = []
        for sport_key in self._active_tournament_keys():
            events.extend(self._get_odds_for_sport(sport_key, params))
        return events

    @staticmethod
    def best_prices(event: dict[str, Any]) -> dict[str, tuple[str, float]] | None:
        """Given one event payload, return {player_name: (bookmaker, best_decimal_odds)}."""
        best: dict[str, tuple[str, float]] = {}
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    name, price = outcome["name"], float(outcome["price"])
                    if name not in best or price > best[name][1]:
                        best[name] = (bookmaker["title"], price)
        return best or None
