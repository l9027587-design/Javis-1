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

    def get_odds(self, regions: str = "eu,uk,us", markets: str = "h2h,totals,btts") -> list[dict[str, Any]]:
        """Returns bookmaker 1X2/totals/BTTS odds for every configured league."""
        params = {"regions": regions, "markets": markets, "oddsFormat": "decimal"}
        events: list[dict[str, Any]] = []
        for sport_key in SPORT_KEYS:
            events.extend(self._get_odds_for_sport(sport_key, params))
        return events

    def get_odds_for_bookmakers(self, bookmakers: str, markets: str = "h2h,totals,btts") -> list[dict[str, Any]]:
        """Same as get_odds(), filtered to specific bookmaker keys (e.g. 'tipico_de').

        The Odds API carries Tipico as a licensed bookmaker feed, so this is the
        ToS-compliant way to get "Tipico odds" — we never scrape tipico.de directly,
        which would violate their terms of service and is explicitly the kind of thing
        this project avoids (see ARCHITECTURE.md). Note: The Odds API bills by
        markets-requested count, so asking for all three markets here costs 3x what a
        h2h-only call did -- worth knowing if odds sync starts erroring on quota.
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

    @staticmethod
    def best_totals(event: dict[str, Any]) -> tuple[str, float, float, float] | None:
        """Given one event payload, return (bookmaker, line, best_over_price, best_under_price)
        for the totals (Über/Unter goals) market, or None if no bookmaker has it priced.

        Bookmakers can each quote a different line (e.g. 2.5 vs 2.75) -- picks whichever
        single bookmaker offers the best over price, and uses that same bookmaker's line
        and under price, rather than mixing lines/prices across bookmakers.
        """
        best: tuple[str, float, float, float] | None = None
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                outcomes = market.get("outcomes", [])
                over = next((o for o in outcomes if o.get("name") == "Over"), None)
                under = next((o for o in outcomes if o.get("name") == "Under"), None)
                if not over or not under:
                    continue
                over_price = float(over["price"])
                if best is None or over_price > best[2]:
                    best = (bookmaker["title"], float(over.get("point", 2.5)), over_price, float(under["price"]))
        return best

    @staticmethod
    def best_btts(event: dict[str, Any]) -> tuple[str, float, float] | None:
        """Given one event payload, return (bookmaker, best_yes_price, best_no_price)
        for the BTTS (both teams to score) market, or None if not priced."""
        best_yes: tuple[str, float] | None = None
        best_no: float | None = None
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "btts":
                    continue
                outcomes = market.get("outcomes", [])
                yes = next((o for o in outcomes if o.get("name") == "Yes"), None)
                no = next((o for o in outcomes if o.get("name") == "No"), None)
                if not yes or not no:
                    continue
                yes_price = float(yes["price"])
                if best_yes is None or yes_price > best_yes[1]:
                    best_yes = (bookmaker["title"], yes_price)
                    best_no = float(no["price"])
        return (best_yes[0], best_yes[1], best_no) if best_yes and best_no is not None else None
