"""SQLAlchemy ORM models for the tennis data store."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plays: Mapped[str | None] = mapped_column(String(16), nullable=True)  # e.g. "right-handed"
    birth_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    current_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )
    # Set by ingest.sync_player_results after a successful past-matches fetch, so repeat
    # ingestion runs can skip players already backfilled recently instead of re-spending
    # a stats-API call on them every run (see RESULTS_FRESHNESS in ingest.py).
    results_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class RankingHistory(Base):
    __tablename__ = "ranking_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    as_of_date: Mapped[dt.date] = mapped_column(Date)
    rank: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)

    __table_args__ = (UniqueConstraint("player_id", "as_of_date"),)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tournament_name: Mapped[str] = mapped_column(String(128))
    surface: Mapped[str | None] = mapped_column(String(32), nullable=True)
    round: Mapped[str | None] = mapped_column(String(32), nullable=True)
    best_of: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(24), default="scheduled")  # scheduled|live|finished
    start_time: Mapped[dt.datetime] = mapped_column(DateTime, index=True)

    player1_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    player2_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    score: Mapped[str | None] = mapped_column(String(64), nullable=True)

    player1: Mapped[Player] = relationship(foreign_keys=[player1_id])
    player2: Mapped[Player] = relationship(foreign_keys=[player2_id])


class MatchStats(Base):
    __tablename__ = "match_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    aces: Mapped[int | None] = mapped_column(Integer, nullable=True)
    double_faults: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_serve_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_serve_won_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    break_points_saved_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (UniqueConstraint("match_id", "player_id"),)


class Odds(Base):
    __tablename__ = "odds"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(64))
    player1_decimal_odds: Mapped[float] = mapped_column(Float)
    player2_decimal_odds: Mapped[float] = mapped_column(Float)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, index=True)


class SyncState(Base):
    """Tracks when a shared (non-player-specific) resource was last successfully fetched,
    e.g. `rankings:atp` -- lets ingest.py skip re-fetching things that don't change
    within a day, so a limited daily API quota goes toward fresher data instead."""

    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    model_version: Mapped[str] = mapped_column(String(32))
    player1_win_prob: Mapped[float] = mapped_column(Float)
    best_player1_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_player2_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # for the favored side
    is_value_bet: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, index=True)
