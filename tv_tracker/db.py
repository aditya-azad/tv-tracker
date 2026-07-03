"""Database engine, session management, and schema initialisation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from tv_tracker.config import settings
from tv_tracker.models import Base


def _build_url(db_path) -> str:
    return f"sqlite:///{db_path}"


def get_engine(db_path=None) -> Engine:
    """Create a SQLAlchemy engine for the SQLite database at *db_path*.

    The parent directory is created automatically if it does not exist.
    """
    path = db_path if db_path is not None else settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        _build_url(path),
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(db_path=None) -> Engine:
    """Initialise the global engine and session factory (idempotent)."""
    global _engine, _SessionLocal
    if _engine is None:
        _engine = get_engine(db_path)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db(db_path=None) -> Engine:
    """Create all tables and return the engine."""
    engine = init_engine(db_path)
    Base.metadata.create_all(engine)
    return engine


def get_session() -> Session:
    """Return a new SQLAlchemy session from the global factory."""
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
