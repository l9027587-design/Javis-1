"""One ingestion cycle: fixtures -> matches, standings -> teams, odds -> odds table.

Designed to be called repeatedly on a schedule (see .github/workflows/pipeline.yml).
Each call is idempotent: teams/matches are upserted by external_id, odds are appended
as a new snapshot so line movement is preserved over time. Fixtures/standings come from
football-data.org (football_client.py); odds come from The Odds API.
"""
from __future__ import annotations

import datetime as dt
import logging

import requests
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased

from src.config import settings
from src.data_pipeline import football_client
from src.data_pipeline.odds_client import OddsAPIClient
from src.db.models import ComboBet, ComboLeg, Match, Odds, SyncState, Team
from src.db.session import get_session, init_db

logger = logging.getLogger(__name__)

# Finished-match backfill and league standings barely change within a day, so skip
# re-fetching them on every ingestion run within this window -- keeps the free-tier
# 10-req/min football-data.org quota spent mostly on the upcoming schedule instead.
RESULTS_FRESHNESS = dt.timedelta(hours=20)
STANDINGS_FRESHNESS = dt.timedelta(hours=20)


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


def _upsert_team(session: Session, external_id: str, name: str) -> Team:
    team = session.scalar(select(Team).where(Team.external_id == external_id))
    if team is None:
        # The same real team can show up under a different external_id if a fixture's
        # team block is ever missing an id -- match by exact name to an existing row
        # instead of creating a duplicate that would fragment standings/H2H data.
        team = session.scalar(select(Team).where(Team.name == name))
    if team is None:
        team = Team(external_id=external_id, name=name)
        session.add(team)
    team.name = name
    session.flush()
    return team


def sync_schedule(session: Session, league_code: str, league_name: str, days_ahead: int = 10) -> int:
    """Fetch upcoming fixtures for one league and upsert them."""
    try:
        raw_fixtures = football_client.get_upcoming_fixtures(league_code, days_ahead=days_ahead)
    except (requests.RequestException, KeyError, AttributeError, ValueError) as exc:
        logger.warning("Football schedule unavailable for league=%s: %s", league_name, exc)
        return 0

    count = 0
    for raw in raw_fixtures:
        try:
            fixture_id = raw.get("id")
            if fixture_id is None:
                continue
            start_raw = raw.get("utcDate")
            if not start_raw:
                continue
            start = dt.datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))

            home_raw, away_raw = raw.get("homeTeam") or {}, raw.get("awayTeam") or {}
            if home_raw.get("id") is None or away_raw.get("id") is None:
                continue
            home = _upsert_team(session, f"fdorg:{home_raw['id']}", home_raw.get("name") or "TBD")
            away = _upsert_team(session, f"fdorg:{away_raw['id']}", away_raw.get("name") or "TBD")

            external_id = f"fdorg:{fixture_id}"
            match = session.scalar(select(Match).where(Match.external_id == external_id))
            if match is None:
                match = Match(external_id=external_id, home_team_id=home.id, away_team_id=away.id)
                session.add(match)

            matchday = raw.get("matchday")
            match.league_name = league_name
            match.round = f"Matchday {matchday}" if matchday is not None else None
            match.status = "scheduled"
            match.start_time = start
            match.home_team_id = home.id
            match.away_team_id = away.id
            session.flush()
            count += 1
        except (KeyError, AttributeError, ValueError) as exc:
            logger.warning("Football schedule fixture unparseable for league=%s: %s", league_name, exc)
    logger.info("Synced %d upcoming fixtures for league=%s", count, league_name)
    return count


def sync_results(session: Session, league_code: str, league_name: str) -> int:
    """Backfill finished fixtures (with score + winner) for one league, used as training data."""
    sync_key = f"results:{league_code}"
    if _recently_synced(session, sync_key, RESULTS_FRESHNESS):
        logger.info("Results for league=%s synced within the last %s, skipping", league_name, RESULTS_FRESHNESS)
        return 0

    try:
        raw_fixtures = football_client.get_recent_results(league_code, days_back=45)
    except (requests.RequestException, KeyError, AttributeError, ValueError) as exc:
        logger.warning("Football results unavailable for league=%s: %s", league_name, exc)
        return 0

    count = 0
    for raw in raw_fixtures:
        try:
            fixture_id = raw.get("id")
            if fixture_id is None or raw.get("status") != "FINISHED":
                continue
            start_raw = raw.get("utcDate")
            if not start_raw:
                continue
            start = dt.datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))

            home_raw, away_raw = raw.get("homeTeam") or {}, raw.get("awayTeam") or {}
            if home_raw.get("id") is None or away_raw.get("id") is None:
                continue

            full_time = (raw.get("score") or {}).get("fullTime") or {}
            home_score, away_score = full_time.get("home"), full_time.get("away")
            if home_score is None or away_score is None:
                continue

            home = _upsert_team(session, f"fdorg:{home_raw['id']}", home_raw.get("name") or "TBD")
            away = _upsert_team(session, f"fdorg:{away_raw['id']}", away_raw.get("name") or "TBD")

            external_id = f"fdorg:{fixture_id}"
            match = session.scalar(select(Match).where(Match.external_id == external_id))
            if match is None:
                match = Match(external_id=external_id, home_team_id=home.id, away_team_id=away.id)
                session.add(match)

            matchday = raw.get("matchday")
            match.league_name = league_name
            match.round = f"Matchday {matchday}" if matchday is not None else None
            match.status = "finished"
            match.start_time = start
            match.home_team_id = home.id
            match.away_team_id = away.id
            match.home_score = home_score
            match.away_score = away_score
            if home_score > away_score:
                match.winner_team_id = home.id
            elif away_score > home_score:
                match.winner_team_id = away.id
            else:
                match.winner_team_id = None  # draw
            session.flush()
            count += 1
        except (KeyError, AttributeError, ValueError) as exc:
            logger.warning("Football result fixture unparseable for league=%s: %s", league_name, exc)

    _mark_synced(session, sync_key)
    logger.info("Synced %d finished results for league=%s", count, league_name)
    return count


def sync_standings(session: Session, league_code: str, league_name: str) -> int:
    """Update each team's current league position/points."""
    sync_key = f"standings:{league_code}"
    if _recently_synced(session, sync_key, STANDINGS_FRESHNESS):
        logger.info("Standings for league=%s synced within the last %s, skipping", league_name, STANDINGS_FRESHNESS)
        return 0

    try:
        table = football_client.get_standings(league_code)
    except (requests.RequestException, KeyError, AttributeError, ValueError) as exc:
        logger.warning("Standings unavailable for league=%s: %s", league_name, exc)
        return 0

    count = 0
    try:
        for entry in table:
            team_raw = entry.get("team") or {}
            if team_raw.get("id") is None:
                continue
            team = _upsert_team(session, f"fdorg:{team_raw['id']}", team_raw.get("name") or "TBD")
            team.league_position = entry.get("position")
            team.league_points = entry.get("points")
            count += 1
    except (KeyError, AttributeError, ValueError) as exc:
        logger.warning("Standings unparseable for league=%s: %s", league_name, exc)

    _mark_synced(session, sync_key)
    logger.info("Synced %d team standings for league=%s", count, league_name)
    return count


def sync_odds(session: Session, odds_client: OddsAPIClient) -> int:
    """Fetch current 1X2 odds and record a snapshot per match found by fuzzy name match.

    Prefers Tipico's feed (settings.odds_bookmakers, default "tipico_de") since that's
    the requested data source; falls back to the broader eu/uk/us region odds for
    matches Tipico doesn't currently list.
    """
    events = odds_client.get_odds_for_bookmakers(settings.odds_bookmakers) if settings.odds_bookmakers else []
    if not events:
        events = odds_client.get_odds()
    logger.info("Fetched %d odds events", len(events))

    count = 0
    unmatched_samples: list[tuple[str, str]] = []
    HomeTeam, AwayTeam = aliased(Team), aliased(Team)
    for event in events:
        home_name, away_name = event.get("home_team"), event.get("away_team")
        if not home_name or not away_name:
            continue
        best = OddsAPIClient.best_prices(event)
        if not best or home_name not in best or away_name not in best:
            continue

        # Football has a real home/away side, but The Odds API's naming still might not
        # line up exactly with whichever team we stored as home vs away for this fixture
        # (e.g. a neutral-venue cup match) -- match either orientation, same rationale as
        # the tennis version of this function had.
        match = session.scalar(
            select(Match)
            .join(HomeTeam, Match.home_team_id == HomeTeam.id)
            .join(AwayTeam, Match.away_team_id == AwayTeam.id)
            .where(
                or_(
                    and_(HomeTeam.name.ilike(f"%{home_name}%"), AwayTeam.name.ilike(f"%{away_name}%")),
                    and_(HomeTeam.name.ilike(f"%{away_name}%"), AwayTeam.name.ilike(f"%{home_name}%")),
                )
            )
        )
        if match is None:
            if len(unmatched_samples) < 5:
                unmatched_samples.append((home_name, away_name))
            continue

        home_is_stored_home = home_name.lower() in match.home_team.name.lower() or match.home_team.name.lower() in home_name.lower()
        bookmaker, home_side_odds = best[home_name]
        _, away_side_odds = best[away_name]
        home_odds, away_odds = (home_side_odds, away_side_odds) if home_is_stored_home else (away_side_odds, home_side_odds)
        draw_entry = best.get("Draw")

        totals = OddsAPIClient.best_totals(event)
        btts = OddsAPIClient.best_btts(event)

        session.add(
            Odds(
                match_id=match.id,
                bookmaker=bookmaker,
                home_decimal_odds=home_odds,
                draw_decimal_odds=draw_entry[1] if draw_entry else None,
                away_decimal_odds=away_odds,
                total_line=totals[1] if totals else None,
                over_decimal_odds=totals[2] if totals else None,
                under_decimal_odds=totals[3] if totals else None,
                btts_yes_odds=btts[1] if btts else None,
                btts_no_odds=btts[2] if btts else None,
            )
        )
        count += 1
    if count == 0 and events:
        logger.warning(
            "Odds events fetched but none matched existing matches; sample event pairings: %s",
            unmatched_samples,
        )
    logger.info("Recorded %d odds snapshots", count)
    return count


def settle_combo_bets(session: Session) -> int:
    """Mark pending combo legs won/lost once their match has finished, and mark the
    whole combo won/lost once every one of its legs is settled (a combo only wins if
    every leg does -- same rule any bookmaker's combo bet uses)."""
    pending_legs = session.scalars(select(ComboLeg).where(ComboLeg.status == "pending")).all()
    touched_combo_ids: set[int] = set()
    for leg in pending_legs:
        match = leg.match
        if match.status != "finished":
            continue
        if leg.pick_side == "home":
            won = match.winner_team_id == match.home_team_id
        elif leg.pick_side == "away":
            won = match.winner_team_id == match.away_team_id
        else:  # draw
            won = match.winner_team_id is None
        leg.status = "won" if won else "lost"
        touched_combo_ids.add(leg.combo_id)

    count = 0
    for combo_id in touched_combo_ids:
        combo = session.get(ComboBet, combo_id)
        if combo is None or combo.status != "pending":
            continue
        leg_statuses = [leg.status for leg in combo.legs]
        if any(status == "pending" for status in leg_statuses):
            continue  # some legs' matches haven't been played yet
        combo.status = "won" if all(status == "won" for status in leg_statuses) else "lost"
        combo.settled_at = dt.datetime.utcnow()
        count += 1
    session.flush()
    logger.info("Settled %d combo bets", count)
    return count


def run_ingestion(days_ahead: int = 7) -> dict[str, int]:
    """Full ingestion cycle across the configured leagues: schedule, results, standings, odds."""
    init_db()

    results = {"matches": 0, "results": 0, "standings": 0, "odds": 0, "combos_settled": 0}
    with get_session() as session:
        for league_code, league_name in football_client.DEFAULT_LEAGUE_CODES.items():
            results["matches"] += sync_schedule(session, league_code, league_name, days_ahead=days_ahead)
            results["results"] += sync_results(session, league_code, league_name)
            results["standings"] += sync_standings(session, league_code, league_name)
        results["odds"] += sync_odds(session, OddsAPIClient())
        results["combos_settled"] += settle_combo_bets(session)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_ingestion())
