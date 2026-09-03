"""Engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import Settings, get_settings
from .models import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def build_engine(settings: Settings | None = None) -> Engine:
    """Create an engine for the configured database.

    SQLite needs two adjustments the other backends do not:
    ``check_same_thread=False`` because FastAPI serves requests from a thread
    pool, and an explicit ``PRAGMA foreign_keys=ON``, since SQLite ignores
    foreign keys by default and would silently leave orphaned spans behind
    when a trace is deleted.
    """
    settings = settings or get_settings()
    kwargs: dict = {"future": True, "pool_pre_ping": True}

    if settings.is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
        path = settings.database_url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(settings.database_url, **kwargs)

    if settings.is_sqlite:

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(connection, _record):  # pragma: no cover - driver hook
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def configure(engine: Engine) -> None:
    """Point the process at a specific engine. Used by tests and by scripts."""
    global _engine, _session_factory
    _engine = engine
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)


def create_all(engine: Engine | None = None) -> None:
    """Create the schema directly.

    Alembic owns migrations for anything long-lived; this exists for tests and
    for the first local run, where waiting on a migration to see a health check
    respond is friction with no payoff.
    """
    Base.metadata.create_all(engine or get_engine())


def drop_all(engine: Engine | None = None) -> None:
    Base.metadata.drop_all(engine or get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transaction that commits on success and rolls back on any exception."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session
