"""Client for The Odds API (https://the-odds-api.com) — h2h (moneyline) tennis odds."""
from __future__ import annotations

from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings

BASE_URL = "https://api.the-odds-api.com/v4"


class OddsAPIClient:
    def __init__(self, sport_key: str = "tennis_atp") -> None:
        """sport_key examples: 'tennis_atp', 'tennis_wta'."""
        self.sport_key = sport_key
        self.session = requests.Session()

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def get_odds(self, regions: str = "eu,uk,us", markets: str = "h2h") -> list[dict[str, Any]]:
        """Returns a list of events with bookmaker h2h (moneyline) odds."""
        url = f"{BASE_URL}/sports/{self.sport_key}/odds"
        params = {
            "apiKey": settings.odds_api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": "decimal",
        }
        response = self.session.get(url, params=params, timeout=15)
        response.raise_for_status()
        return response.json()

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
