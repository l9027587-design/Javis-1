"""SQLAlchemy ORM models for the football data store."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Current league standing, when known -- same idea as a tennis rank, just per
    # competition rather than a global number. Optional: the schedule sync only has
    # team names, not standings, so this is filled in by a separate standings sync.
    league_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    league_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    league_name: Mapped[str] = mapped_column(String(128))
    round: Mapped[str | None] = mapped_column(String(32), nullable=True)  # e.g. "Matchday 5"
    status: Mapped[str] = mapped_column(String(24), default="scheduled")  # scheduled|finished
    start_time: Mapped[dt.datetime] = mapped_column(DateTime, index=True)

    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    # Null even when finished means a draw -- don't treat "no winner" as "not finished".
    winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])
    winner_team: Mapped[Team | None] = relationship(foreign_keys=[winner_team_id])


class Odds(Base):
    __tablename__ = "odds"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(64))
    # 1X2 (match winner) market.
    home_decimal_odds: Mapped[float] = mapped_column(Float)
    draw_decimal_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_decimal_odds: Mapped[float] = mapped_column(Float)
    # Totals (Über/Unter) market -- one line (e.g. 2.5 goals) with over/under prices.
    # Nullable as a whole: not every bookmaker/match has this market priced.
    total_line: Mapped[float | None] = mapped_column(Float, nullable=True)
    over_decimal_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    under_decimal_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Both Teams To Score (BTTS) market.
    btts_yes_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    btts_no_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, index=True)


class SyncState(Base):
    """Tracks when a shared (non-team-specific) resource was last successfully fetched,
    e.g. 'fixtures:39' (league id 39) -- lets ingest.py skip re-fetching things that
    don't change within a day, so a limited daily API quota goes toward fresher data."""

    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    model_version: Mapped[str] = mapped_column(String(32))
    # 1X2 market: the three probabilities sum to ~1.
    home_win_prob: Mapped[float] = mapped_column(Float)
    draw_prob: Mapped[float] = mapped_column(Float)
    away_win_prob: Mapped[float] = mapped_column(Float)
    best_home_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_draw_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_away_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # for the best-edge outcome
    value_pick: Mapped[str | None] = mapped_column(String(8), nullable=True)  # "home"|"draw"|"away"
    is_value_bet: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, index=True)
