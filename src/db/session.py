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
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # Superseded by tour-level freshness tracking in sync_state (see ingest.py) once
        # match-history backfill moved from a per-player API walk to a single per-tour
        # dataset fetch -- drop the now-unused column from any DB it already reached.
        conn.execute(text("ALTER TABLE players DROP COLUMN IF EXISTS results_synced_at"))


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
