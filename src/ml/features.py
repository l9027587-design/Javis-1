"""Feature engineering: turns (player_a, player_b, as_of_date) into a model input row.

Every feature is expressed as "player_a minus/relative-to player_b" so the model
learns a symmetric function of the matchup rather than positional bias. Historical
matches are trained on both (winner, loser)->1 and (loser, winner)->0 orderings.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Match, Player

FEATURE_COLUMNS = [
    "rank_diff",
    "points_diff",
    "recent_form_diff",
    "surface_winrate_diff",
    "h2h_winrate_diff",
    "days_since_last_match_diff",
]


def _recent_form(session: Session, player_id: int, before: dt.datetime, n: int = 10) -> float:
    """Win rate over the player's last n completed matches before `before`."""
    matches = session.scalars(
        select(Match)
        .where(
            Match.status == "finished",
            Match.start_time < before,
            (Match.player1_id == player_id) | (Match.player2_id == player_id),
        )
        .order_by(Match.start_time.desc())
        .limit(n)
    ).all()
    if not matches:
        return 0.5
    wins = sum(1 for m in matches if m.winner_id == player_id)
    return wins / len(matches)


def _surface_winrate(session: Session, player_id: int, surface: str | None, before: dt.datetime) -> float:
    if not surface:
        return 0.5
    matches = session.scalars(
        select(Match).where(
            Match.status == "finished",
            Match.start_time < before,
            Match.surface == surface,
            (Match.player1_id == player_id) | (Match.player2_id == player_id),
        )
    ).all()
    if not matches:
        return 0.5
    wins = sum(1 for m in matches if m.winner_id == player_id)
    return wins / len(matches)


def _h2h_winrate(session: Session, player_id: int, opponent_id: int, before: dt.datetime) -> float:
    matches = session.scalars(
        select(Match).where(
            Match.status == "finished",
            Match.start_time < before,
            (
                ((Match.player1_id == player_id) & (Match.player2_id == opponent_id))
                | ((Match.player1_id == opponent_id) & (Match.player2_id == player_id))
            ),
        )
    ).all()
    if not matches:
        return 0.5
    wins = sum(1 for m in matches if m.winner_id == player_id)
    return wins / len(matches)


def _days_since_last_match(session: Session, player_id: int, before: dt.datetime) -> float:
    last = session.scalar(
        select(Match)
        .where(
            Match.status == "finished",
            Match.start_time < before,
            (Match.player1_id == player_id) | (Match.player2_id == player_id),
        )
        .order_by(Match.start_time.desc())
        .limit(1)
    )
    if last is None:
        return 30.0  # assume a month of rest if no history
    return max((before - last.start_time).days, 0)


def build_features(
    session: Session, player_a: Player, player_b: Player, surface: str | None, as_of: dt.datetime
) -> dict[str, float]:
    """Single-row feature dict for player_a vs player_b as of `as_of`."""
    rank_a = player_a.current_rank or 300
    rank_b = player_b.current_rank or 300
    points_a = player_a.current_points or 0
    points_b = player_b.current_points or 0

    return {
        "rank_diff": rank_b - rank_a,  # positive => player_a ranked higher (lower number = better, so invert)
        "points_diff": points_a - points_b,
        "recent_form_diff": _recent_form(session, player_a.id, as_of) - _recent_form(session, player_b.id, as_of),
        "surface_winrate_diff": (
            _surface_winrate(session, player_a.id, surface, as_of)
            - _surface_winrate(session, player_b.id, surface, as_of)
        ),
        "h2h_winrate_diff": _h2h_winrate(session, player_a.id, player_b.id, as_of) - 0.5,
        "days_since_last_match_diff": (
            _days_since_last_match(session, player_a.id, as_of) - _days_since_last_match(session, player_b.id, as_of)
        ),
    }


def build_training_dataframe(session: Session) -> pd.DataFrame:
    """Every finished match, duplicated as both (winner,loser)->1 and (loser,winner)->0 rows."""
    finished = session.scalars(select(Match).where(Match.status == "finished", Match.winner_id.is_not(None))).all()

    rows = []
    for match in finished:
        p1, p2 = match.player1, match.player2
        if match.winner_id == p1.id:
            winner, loser = p1, p2
        else:
            winner, loser = p2, p1

        row_win = build_features(session, winner, loser, match.surface, match.start_time)
        row_win["label"] = 1
        rows.append(row_win)

        row_loss = build_features(session, loser, winner, match.surface, match.start_time)
        row_loss["label"] = 0
        rows.append(row_loss)

    return pd.DataFrame(rows, columns=[*FEATURE_COLUMNS, "label"])
