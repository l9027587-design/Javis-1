"""One ingestion cycle: rankings -> players, schedule -> matches, odds -> odds table.

Designed to be called repeatedly on a schedule (see cloud/lambda_ingest_handler.py).
Each call is idempotent: players/matches are upserted by external_id, odds are
appended as a new snapshot so line movement is preserved over time. Rankings and
finished-match backfill come from Jeff Sackmann's free dataset (sackmann_client.py)
rather than the metered live stats API, which is reserved for the upcoming schedule.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import pandas as pd
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from src.config import settings
from src.data_pipeline import sackmann_client
from src.data_pipeline.api_client import TennisAPIClient
from src.data_pipeline.odds_client import OddsAPIClient
from src.db.models import Match, Odds, Player, SyncState
from src.db.session import get_session, init_db

logger = logging.getLogger(__name__)

# Rankings and season match history (Sackmann dataset) barely change within a day, so
# skip re-downloading/re-parsing them on every ingestion run within this window -- avoids
# needless work now that they're free, though the live stats API's quota (spent on
# sync_schedule) is the more important thing this used to protect.
RANKINGS_FRESHNESS = dt.timedelta(hours=20)
RESULTS_FRESHNESS = dt.timedelta(hours=20)


def _recently_synced(session: Session, key: str, freshness: dt.timedelta) -> bool:
    state = session.get(SyncState, key)
    return state is not None and dt.datetime.utcnow() - state.synced_at < freshness


def _mark_synced(session: Session, key: str) -> None:
    state = session.get(SyncState, key)
    if state is None:
        session.add(SyncState(key=key, synced_at=dt.datetime.utcnow()))
    else:
        state.synced_at = dt.datetime.utcnow()
    session.flush()


def _upsert_player(session: Session, external_id: str, name: str, rank: int | None, points: int | None) -> Player:
    player = session.scalar(select(Player).where(Player.external_id == external_id))
    if player is None:
        # The same real player can show up under a different external_id per source
        # (e.g. Sackmann's dataset ID vs. the live schedule API's own numeric ID) --
        # match by exact name to an existing row instead of creating a duplicate that
        # would fragment rank/H2H data across two Player rows for one person.
        player = session.scalar(select(Player).where(Player.name == name))
    if player is None:
        player = Player(external_id=external_id, name=name)
        session.add(player)
    player.name = name
    # Callers that don't know rank/points (e.g. schedule sync, which only has names)
    # pass None -- don't let that clobber values sync_rankings already set.
    if rank is not None:
        player.current_rank = rank
    if points is not None:
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


def sync_rankings(session: Session, tour: str = "atp") -> int:
    """Rankings from Jeff Sackmann's free dataset (see sackmann_client.py) instead of the
    metered live stats API -- ATP/WTA rankings only move weekly anyway, so a dataset with
    a few days' lag costs nothing in accuracy and zero API quota. Returns count synced."""
    sync_key = f"rankings:{tour}"
    if _recently_synced(session, sync_key, RANKINGS_FRESHNESS):
        logger.info("Rankings for tour=%s synced within the last %s, skipping", tour, RANKINGS_FRESHNESS)
        return 0
    try:
        players_df = sackmann_client.get_players(tour)
        rankings_df = sackmann_client.get_current_rankings(tour)

        latest_date = rankings_df["ranking_date"].max()
        latest = rankings_df[rankings_df["ranking_date"] == latest_date]
        players_by_id = players_df.set_index("player_id")

        count = 0
        for row in latest.itertuples():
            if row.player not in players_by_id.index:
                continue
            info = players_by_id.loc[row.player]
            name = f"{info.name_first} {info.name_last}".strip()
            if not name or pd.isna(row.rank):
                continue
            points = None if pd.isna(row.points) else int(row.points)
            _upsert_player(session, f"sackmann:{row.player}", name, int(row.rank), points)
            count += 1
    except (requests.RequestException, FileNotFoundError, KeyError, AttributeError, ValueError) as exc:
        # Network failure, a filename sackmann_client couldn't resolve, a column that
        # doesn't parse as expected (e.g. non-numeric rank/points), or the dataset's
        # column layout doesn't match what this parses (its schema is a documented but
        # unversioned convention, not a contract) -- skip this tour's rankings for now
        # rather than aborting the whole ingestion run over it.
        logger.warning("Sackmann rankings dataset unavailable/unparseable for tour=%s: %s", tour, exc)
        return 0
    _mark_synced(session, sync_key)
    logger.info("Synced %d player rankings for tour=%s (Sackmann dataset)", count, tour)
    return count


def sync_schedule(session: Session, client: TennisAPIClient, date: str, tour: str = "atp") -> int:
    """Fetch matches scheduled for `date` (YYYY-MM-DD) on one tour and upsert them."""
    try:
        data = client.get_schedule(date, tour=tour)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (403, 429):
            # Some plans only expose fixtures within a limited days-ahead window (403),
            # or the rate limit is still exhausted after _get's own retries (429); a
            # single date shouldn't abort the whole ingestion run either way.
            logger.warning(
                "Schedule for tour=%s date=%s unavailable (HTTP %d), skipping",
                tour, date, exc.response.status_code,
            )
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


def sync_player_results(session: Session, tour: str) -> int:
    """Backfill finished matches (with winner + score) from Jeff Sackmann's dataset.

    Replaces the old per-player "past matches" API walk (~20 of ~28 stats-API calls per
    ingestion run) with a single free CSV fetch covering the whole tour's season, so
    src/ml/train.py gets far more training data at zero API cost. Pulls the current and
    previous year's files so results near a season boundary aren't missed.
    """
    sync_key = f"results:{tour}"
    if _recently_synced(session, sync_key, RESULTS_FRESHNESS):
        logger.info("Results for tour=%s synced within the last %s, skipping", tour, RESULTS_FRESHNESS)
        return 0

    this_year = dt.date.today().year
    frames = []
    for year in (this_year, this_year - 1):
        try:
            frames.append(sackmann_client.get_matches(tour, year))
        except (requests.RequestException, FileNotFoundError) as exc:
            logger.warning("Sackmann matches dataset unavailable for tour=%s year=%d: %s", tour, year, exc)
    if not frames:
        return 0

    count = 0
    try:
        matches_df = pd.concat(frames, ignore_index=True)
        for row in matches_df.itertuples():
            if pd.isna(row.winner_id) or pd.isna(row.loser_id) or pd.isna(row.tourney_date):
                continue
            external_id = f"sackmann:{tour}:{row.tourney_id}:{row.match_num}"

            # Player IDs are just namespaced into an external_id string here, not used
            # numerically -- don't assume they're integers (TML-Database's are
            # alphanumeric, e.g. "B0BI", unlike Sackmann's plain integer IDs).
            p1 = _upsert_player(session, f"sackmann:{row.winner_id}", row.winner_name, None, None)
            p2 = _upsert_player(session, f"sackmann:{row.loser_id}", row.loser_name, None, None)

            match = session.scalar(select(Match).where(Match.external_id == external_id))
            if match is None:
                match = Match(external_id=external_id, player1_id=p1.id, player2_id=p2.id)
                session.add(match)

            match.tournament_name = row.tourney_name if isinstance(row.tourney_name, str) else "Unknown"
            match.surface = row.surface if isinstance(row.surface, str) else None
            match.round = row.round if isinstance(row.round, str) else None
            match.status = "finished"
            match.winner_id = p1.id
            match.score = row.score if isinstance(row.score, str) else None
            match.start_time = dt.datetime.strptime(str(int(row.tourney_date)), "%Y%m%d")
            match.player1_id = p1.id
            match.player2_id = p2.id
            session.flush()
            count += 1
    except (KeyError, AttributeError, ValueError) as exc:
        # Same rationale as sync_rankings: don't let an unparseable dataset column
        # abort the whole ingestion run -- keep whatever rows were synced before the
        # error and move on.
        logger.warning("Sackmann matches dataset unparseable for tour=%s: %s", tour, exc)

    _mark_synced(session, sync_key)
    logger.info("Synced %d finished results for tour=%s (Sackmann dataset)", count, tour)
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
    """Full ingestion cycle: rankings, next `days_ahead` days of schedule, results, and odds."""
    init_db()
    client = TennisAPIClient()

    results = {"players": 0, "matches": 0, "results": 0, "odds": 0}
    with get_session() as session:
        today = dt.date.today()
        for tour in ("atp", "wta"):
            results["players"] += sync_rankings(session, tour=tour)
            for offset in range(days_ahead):
                date_str = (today + dt.timedelta(days=offset)).isoformat()
                results["matches"] += sync_schedule(session, client, date_str, tour=tour)
            results["results"] += sync_player_results(session, tour=tour)
            results["odds"] += sync_odds(session, OddsAPIClient(tour_prefix=f"tennis_{tour}"))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_ingestion())
