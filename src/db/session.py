"""Engine/session factory + schema bootstrap."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.config import settings
from src.db.models import Base

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create all tables if they don't exist yet, and apply simple column migrations that
    create_all() can't handle on tables that already existed before the model changed."""
    with engine.begin() as conn:
        # One-time migration for the tennis -> football switch: the old schema
        # (players/matches/odds/predictions with tennis-only columns like surface,
        # best_of, player1/player2) can't be adapted by create_all() -- it only adds
        # missing tables, it doesn't rename/alter columns on ones that already exist.
        # "players" existing at all means this DB predates the switch (the new schema
        # never creates that table again), so it's a safe one-shot trigger rather than
        # something that would nuke football data on a later, ordinary init-db re-run.
        if conn.execute(text("SELECT to_regclass('public.players')")).scalar() is not None:
            for old_table in ("match_stats", "ranking_history", "odds", "predictions", "matches", "players"):
                conn.execute(text(f"DROP TABLE IF EXISTS {old_table} CASCADE"))
    Base.metadata.create_all(engine)


@contextmanager
def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
