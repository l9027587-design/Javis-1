"""One ingestion cycle: rankings -> players, schedule -> matches, odds -> odds table.

Designed to be called repeatedly on a schedule (see cloud/lambda_ingest_handler.py).
Each call is idempotent: players/matches are upserted by external_id, odds are
appended as a new snapshot so line movement is preserved over time.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from src.config import settings
from src.data_pipeline.api_client import TennisAPIClient
from src.data_pipeline.odds_client import OddsAPIClient
from src.db.models import Match, Odds, Player
from src.db.session import get_session, init_db

logger = logging.getLogger(__name__)


def _upsert_player(session: Session, external_id: str, name: str, rank: int | None, points: int | None) -> Player:
    player = session.scalar(select(Player).where(Player.external_id == external_id))
    if player is None:
        player = Player(external_id=external_id, name=name)
        session.add(player)
    player.name = name
    player.current_rank = rank
    player.current_points = points
    session.flush()
    return player


def _extract_list(data: Any, *keys: str) -> list:
    """Unwrap a list from a JSON envelope, trying known/likely container keys."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            if isinstance(data.get(key), list):
                return data[key]
    return []


def sync_rankings(session: Session, client: TennisAPIClient, tour: str = "atp") -> int:
    """Fetch current rankings for one tour and upsert players. Returns count synced."""
    data = client.get_rankings(tour=tour)
    entries = _extract_list(data, "rankings", "data", "results", "players")
    if not entries:
        logger.warning(
            "No ranking entries parsed for tour=%s; raw response keys/sample: %s",
            tour,
            list(data.keys()) if isinstance(data, dict) else str(data)[:300],
        )
    count = 0
    for entry in entries:
        competitor = entry.get("player", entry.get("competitor", entry))
        external_id = str(
            competitor.get("id") or competitor.get("playerId") or competitor.get("player_id") or ""
        )
        name = competitor.get("name") or competitor.get("fullName") or competitor.get("full_name", "Unknown")
        rank = entry.get("rank") or entry.get("position")
        points = entry.get("points")
        if not external_id or external_id == "None":
            continue
        _upsert_player(session, external_id, name, rank, points)
        count += 1
    logger.info("Synced %d player rankings for tour=%s", count, tour)
    return count


def sync_schedule(session: Session, client: TennisAPIClient, date: str, tour: str = "atp") -> int:
    """Fetch matches scheduled for `date` (YYYY-MM-DD) on one tour and upsert them."""
    try:
        data = client.get_schedule(date, tour=tour)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 403:
            # Some plans only expose fixtures within a limited days-ahead window; a
            # date beyond that shouldn't abort the whole ingestion run.
            logger.warning("Schedule for tour=%s date=%s forbidden (plan limit?), skipping", tour, date)
            return 0
        raise
    raw_matches = _extract_list(data, "fixtures", "sport_events", "matches", "data", "results")
    if not raw_matches:
        logger.warning(
            "No fixtures parsed for tour=%s date=%s; raw response keys/sample: %s",
            tour,
            date,
            list(data.keys()) if isinstance(data, dict) else str(data)[:300],
        )
    count = 0
    for raw in raw_matches:
        external_id = str(raw.get("id") or raw.get("fixtureId") or raw.get("match_id") or "")
        if not external_id or external_id == "None":
            continue

        competitors = raw.get("competitors") or raw.get("players")
        home = competitors[0] if competitors else raw.get("player1") or raw.get("home") or {}
        away = competitors[1] if competitors else raw.get("player2") or raw.get("away") or {}

        def _player_id(p: dict, fallback: str) -> str:
            return str(p.get("id") or p.get("playerId") or p.get("player_id") or fallback)

        def _player_name(p: dict) -> str:
            return p.get("name") or p.get("fullName") or p.get("full_name") or "TBD"

        p1 = _upsert_player(session, _player_id(home, f"{external_id}-p1"), _player_name(home), None, None)
        p2 = _upsert_player(session, _player_id(away, f"{external_id}-p2"), _player_name(away), None, None)

        match = session.scalar(select(Match).where(Match.external_id == external_id))
        if match is None:
            match = Match(external_id=external_id, player1_id=p1.id, player2_id=p2.id)
            session.add(match)

        tournament = raw.get("tournament") or {}
        match.tournament_name = (
            tournament.get("name") if isinstance(tournament, dict) else None
        ) or raw.get("tournament_name", "Unknown")
        match.surface = raw.get("surface")
        match.round = raw.get("round")
        match.status = raw.get("status", "scheduled")
        start = raw.get("start_time") or raw.get("scheduled") or raw.get("date")
        match.start_time = (
            dt.datetime.fromisoformat(start.replace("Z", "+00:00")) if start else dt.datetime.utcnow()
        )
        match.player1_id = p1.id
        match.player2_id = p2.id
        session.flush()
        count += 1
    logger.info("Synced %d matches for tour=%s date=%s", count, tour, date)
    return count


def sync_odds(session: Session, odds_client: OddsAPIClient) -> int:
    """Fetch current odds and record a snapshot per match found by fuzzy name match.

    Prefers Tipico's feed (settings.odds_bookmakers, default "tipico_de") since that's
    the requested data source; falls back to the broader eu/uk/us region odds for
    matches Tipico doesn't currently list.
    """
    events = odds_client.get_odds_for_bookmakers(settings.odds_bookmakers) if settings.odds_bookmakers else []
    if not events:
        events = odds_client.get_odds()
    count = 0
    for event in events:
        home_name, away_name = event.get("home_team"), event.get("away_team")
        best = OddsAPIClient.best_prices(event)
        if not best or home_name not in best or away_name not in best:
            continue

        home_last, away_last = home_name.split()[-1], away_name.split()[-1]
        Player1, Player2 = aliased(Player), aliased(Player)
        match = session.scalar(
            select(Match)
            .join(Player1, Match.player1_id == Player1.id)
            .join(Player2, Match.player2_id == Player2.id)
            .where(
                Player1.name.ilike(f"%{home_last}%"), Player2.name.ilike(f"%{away_last}%")
            )
        )
        if match is None:
            continue  # no matching row ingested from the stats API yet

        bookmaker, p1_odds = best[home_name]
        _, p2_odds = best[away_name]
        session.add(
            Odds(
                match_id=match.id,
                bookmaker=bookmaker,
                player1_decimal_odds=p1_odds,
                player2_decimal_odds=p2_odds,
            )
        )
        count += 1
    logger.info("Recorded %d odds snapshots", count)
    return count


def run_ingestion(days_ahead: int = 3) -> dict[str, int]:
    """Full ingestion cycle: rankings, next `days_ahead` days of schedule, and odds."""
    init_db()
    client = TennisAPIClient()

    results = {"players": 0, "matches": 0, "odds": 0}
    with get_session() as session:
        today = dt.date.today()
        for tour in ("atp", "wta"):
            results["players"] += sync_rankings(session, client, tour=tour)
            for offset in range(days_ahead):
                date_str = (today + dt.timedelta(days=offset)).isoformat()
                results["matches"] += sync_schedule(session, client, date_str, tour=tour)
            results["odds"] += sync_odds(session, OddsAPIClient(tour_prefix=f"tennis_{tour}"))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_ingestion())
