"""Feature engineering: turns (home_team, away_team, as_of_date) into a model input row.

Unlike tennis, football has a real home/away asymmetry (home advantage is a genuine,
learnable signal), so -- unlike the old tennis pipeline, which duplicated every match as
both (winner,loser)->1 and (loser,winner)->0 rows to cancel out positional bias -- each
finished match here contributes exactly one row, from the home team's perspective.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Match, Team

FEATURE_COLUMNS = [
    "league_position_diff",
    "points_diff",
    "recent_form_diff",
    "goal_diff_avg_diff",
    "h2h_home_win_rate",
    "days_since_last_match_diff",
]


def _recent_matches(session: Session, team_id: int, before: dt.datetime, n: int) -> list[Match]:
    return session.scalars(
        select(Match)
        .where(
            Match.status == "finished",
            Match.start_time < before,
            (Match.home_team_id == team_id) | (Match.away_team_id == team_id),
        )
        .order_by(Match.start_time.desc())
        .limit(n)
    ).all()


def _recent_form(session: Session, team_id: int, before: dt.datetime, n: int = 10) -> float:
    """Points-per-game rate (win=1, draw=0.5, loss=0) over the team's last n matches."""
    matches = _recent_matches(session, team_id, before, n)
    if not matches:
        return 0.5
    points = 0.0
    for m in matches:
        is_home = m.home_team_id == team_id
        if m.home_score == m.away_score:
            points += 0.5
        elif (is_home and m.home_score > m.away_score) or (not is_home and m.away_score > m.home_score):
            points += 1.0
    return points / len(matches)


def _recent_goal_diff(session: Session, team_id: int, before: dt.datetime, n: int = 10) -> float:
    """Average (goals scored - goals conceded) per match over the team's last n matches."""
    matches = _recent_matches(session, team_id, before, n)
    if not matches:
        return 0.0
    total = 0
    for m in matches:
        is_home = m.home_team_id == team_id
        scored = m.home_score if is_home else m.away_score
        conceded = m.away_score if is_home else m.home_score
        total += scored - conceded
    return total / len(matches)


def _h2h_home_win_rate(session: Session, home_id: int, away_id: int, before: dt.datetime) -> float:
    """Points-per-game rate for whichever team is `home_id` in *this* fixture, across
    all past meetings between the two teams regardless of which side they played on."""
    matches = session.scalars(
        select(Match).where(
            Match.status == "finished",
            Match.start_time < before,
            (
                ((Match.home_team_id == home_id) & (Match.away_team_id == away_id))
                | ((Match.home_team_id == away_id) & (Match.away_team_id == home_id))
            ),
        )
    ).all()
    if not matches:
        return 0.5
    points = 0.0
    for m in matches:
        home_id_was_home_side = m.home_team_id == home_id
        if m.home_score == m.away_score:
            points += 0.5
        elif (home_id_was_home_side and m.home_score > m.away_score) or (
            not home_id_was_home_side and m.away_score > m.home_score
        ):
            points += 1.0
    return points / len(matches)


def _days_since_last_match(session: Session, team_id: int, before: dt.datetime) -> float:
    last = _recent_matches(session, team_id, before, n=1)
    if not last:
        return 14.0  # assume two weeks' rest if no history
    return max((before - last[0].start_time).days, 0)


def build_features(session: Session, home_team: Team, away_team: Team, as_of: dt.datetime) -> dict[str, float]:
    """Single-row feature dict for home_team vs away_team as of `as_of`."""
    home_pos = home_team.league_position or 10
    away_pos = away_team.league_position or 10
    home_pts = home_team.league_points or 0
    away_pts = away_team.league_points or 0

    return {
        # Lower position number = better standing, so a positive diff means the home
        # team is ranked higher in the table.
        "league_position_diff": away_pos - home_pos,
        "points_diff": home_pts - away_pts,
        "recent_form_diff": (
            _recent_form(session, home_team.id, as_of) - _recent_form(session, away_team.id, as_of)
        ),
        "goal_diff_avg_diff": (
            _recent_goal_diff(session, home_team.id, as_of) - _recent_goal_diff(session, away_team.id, as_of)
        ),
        "h2h_home_win_rate": _h2h_home_win_rate(session, home_team.id, away_team.id, as_of) - 0.5,
        "days_since_last_match_diff": (
            _days_since_last_match(session, home_team.id, as_of)
            - _days_since_last_match(session, away_team.id, as_of)
        ),
    }


def build_training_dataframe(session: Session) -> pd.DataFrame:
    """Every finished match as one row, labeled 0=away win, 1=draw, 2=home win."""
    finished = session.scalars(
        select(Match).where(
            Match.status == "finished", Match.home_score.is_not(None), Match.away_score.is_not(None)
        )
    ).all()

    rows = []
    for match in finished:
        features = build_features(session, match.home_team, match.away_team, match.start_time)
        if match.home_score > match.away_score:
            label = 2
        elif match.away_score > match.home_score:
            label = 0
        else:
            label = 1
        features["label"] = label
        rows.append(features)

    return pd.DataFrame(rows, columns=[*FEATURE_COLUMNS, "label"])
