"""One ingestion cycle: rankings -> players, schedule -> matches, odds -> odds table.

Designed to be called repeatedly on a schedule (see cloud/lambda_ingest_handler.py).
Each call is idempotent: players/matches are upserted by external_id, odds are
appended as a new snapshot so line movement is preserved over time.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

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


def sync_rankings(session: Session, client: TennisAPIClient) -> int:
    """Fetch current rankings and upsert players. Returns count synced."""
    data = client.get_rankings()
    entries = data.get("rankings", data) if isinstance(data, dict) else data
    count = 0
    for entry in entries:
        competitor = entry.get("competitor", entry)
        external_id = str(competitor.get("id") or competitor.get("player_id"))
        name = competitor.get("name") or competitor.get("full_name", "Unknown")
        rank = entry.get("rank")
        points = entry.get("points")
        if not external_id or external_id == "None":
            continue
        _upsert_player(session, external_id, name, rank, points)
        count += 1
    logger.info("Synced %d player rankings", count)
    return count


def sync_schedule(session: Session, client: TennisAPIClient, date: str) -> int:
    """Fetch matches scheduled for `date` (YYYY-MM-DD) and upsert them."""
    data = client.get_schedule(date)
    raw_matches = data.get("sport_events", data.get("matches", data)) if isinstance(data, dict) else data
    count = 0
    for raw in raw_matches:
        external_id = str(raw.get("id") or raw.get("match_id"))
        if not external_id or external_id == "None":
            continue

        home = raw.get("competitors", [{}, {}])[0] if raw.get("competitors") else raw.get("player1", {})
        away = raw.get("competitors", [{}, {}])[1] if raw.get("competitors") else raw.get("player2", {})

        p1 = _upsert_player(session, str(home.get("id", f"{external_id}-p1")), home.get("name", "TBD"), None, None)
        p2 = _upsert_player(session, str(away.get("id", f"{external_id}-p2")), away.get("name", "TBD"), None, None)

        match = session.scalar(select(Match).where(Match.external_id == external_id))
        if match is None:
            match = Match(external_id=external_id, player1_id=p1.id, player2_id=p2.id)
            session.add(match)

        match.tournament_name = raw.get("tournament", {}).get("name", raw.get("tournament_name", "Unknown"))
        match.surface = raw.get("surface")
        match.round = raw.get("round")
        match.status = raw.get("status", "scheduled")
        start = raw.get("start_time") or raw.get("scheduled")
        match.start_time = dt.datetime.fromisoformat(start.replace("Z", "+00:00")) if start else dt.datetime.utcnow()
        match.player1_id = p1.id
        match.player2_id = p2.id
        session.flush()
        count += 1
    logger.info("Synced %d matches for %s", count, date)
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

        match = session.scalar(
            select(Match)
            .join(Player, Match.player1_id == Player.id)
            .where(Player.name.ilike(f"%{home_name.split()[-1]}%"))
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
    odds_client = OddsAPIClient()

    results = {"players": 0, "matches": 0, "odds": 0}
    with get_session() as session:
        results["players"] = sync_rankings(session, client)
        today = dt.date.today()
        for offset in range(days_ahead):
            date_str = (today + dt.timedelta(days=offset)).isoformat()
            results["matches"] += sync_schedule(session, client, date_str)
        results["odds"] = sync_odds(session, odds_client)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_ingestion())
