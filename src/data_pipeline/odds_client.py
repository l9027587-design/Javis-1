"""Client for The Odds API (https://the-odds-api.com) — 1X2 (home/draw/away) football odds."""
from __future__ import annotations

from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import settings

BASE_URL = "https://api.the-odds-api.com/v4"

# The Odds API keys football per competition, but unlike tennis these are stable,
# enduring sport keys (leagues run for most of the year) rather than ephemeral
# per-tournament ones -- no need to discover "currently in season" keys first, just
# query this fixed list directly. Matches football_client.py's DEFAULT_LEAGUE_IDS.
SPORT_KEYS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
]


class OddsAPIClient:
    def __init__(self) -> None:
        self.session = requests.Session()

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def _get_odds_for_sport(self, sport_key: str, params: dict[str, str]) -> list[dict[str, Any]]:
        url = f"{BASE_URL}/sports/{sport_key}/odds"
        response = self.session.get(url, params={**params, "apiKey": settings.odds_api_key}, timeout=15)
        if response.status_code == 404:
            return []  # this league isn't "in season" right now (e.g. summer break)
        response.raise_for_status()
        return response.json()

    def get_odds(self, regions: str = "eu,uk,us", markets: str = "h2h") -> list[dict[str, Any]]:
        """Returns bookmaker h2h (1X2) odds for every configured league."""
        params = {"regions": regions, "markets": markets, "oddsFormat": "decimal"}
        events: list[dict[str, Any]] = []
        for sport_key in SPORT_KEYS:
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
        for sport_key in SPORT_KEYS:
            events.extend(self._get_odds_for_sport(sport_key, params))
        return events

    @staticmethod
    def best_prices(event: dict[str, Any]) -> dict[str, tuple[str, float]] | None:
        """Given one event payload, return {outcome_name: (bookmaker, best_decimal_odds)}.

        For football's h2h market, outcome names are the two team names plus the
        literal string "Draw" -- unlike tennis, which only ever has two outcomes.
        """
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
