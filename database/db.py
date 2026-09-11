"""
Database engine / session management.

The engine is created lazily on first use so that the HTTP health server
can still boot (and report the problem via /health and /api/status) even
when DATABASE_URL is missing or the database is temporarily unavailable.
"""

import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from config import Config
from database.models import Base

log = logging.getLogger(__name__)

_engine = None
_SessionLocal = None


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when DATABASE_URL is missing."""


def get_engine():
    global _engine, _SessionLocal

    if _engine is None:
        if not Config.database_configured():
            raise DatabaseNotConfiguredError("DATABASE_URL is not configured")
        _engine = create_engine(
            Config.DATABASE_URL,
            pool_pre_ping=True,   # recover from dropped/stale connections
            pool_recycle=300,     # recycle connections every 5 minutes
            pool_size=5,
            max_overflow=5,
        )
        _SessionLocal = sessionmaker(
            bind=_engine, autoflush=False, expire_on_commit=False
        )
        log.info("Database engine created (pool_pre_ping enabled)")
    return _engine


def get_session_factory():
    get_engine()
    return _SessionLocal


@contextmanager
def db_session():
    """
    Provide a transactional database session.

    Commits on success, rolls back on any exception, always closes.
    Safe to use from any thread (polling thread + Flask request threads).
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db():
    """Create all tables if they do not exist yet."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    log.info("Database initialized (tables ensured: tg_chats, tg_users, tg_messages)")


def check_database():
    """Return True when the database answers a trivial query."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 - status checks must never crash
        log.warning("Database check failed: %s", exc.__class__.__name__)
        return False
