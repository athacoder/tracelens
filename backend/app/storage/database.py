"""Engine and session management."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import Settings, get_settings
from .models import Base

logger = logging.getLogger("tracelens.storage")

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


def create_all(engine: Engine | None = None, stamp: bool = True) -> None:
    """Create the schema directly, and record that it is at the latest revision.

    Alembic owns migrations for anything long-lived; this exists for tests and
    for the first local run, where waiting on a migration to see a health check
    respond is friction with no payoff.

    The stamp is what keeps the two paths from colliding. Without it, a database
    created here has every table but no row in ``alembic_version``, so the next
    ``alembic upgrade head`` starts from base and fails with "table traces
    already exists" — which is exactly what happens to anyone who runs the app
    before they run the migrations. CI never sees it, because CI always migrates
    a database nothing has touched.

    Pass ``stamp=False`` in tests that assert on the exact set of tables.
    """
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    if stamp:
        stamp_alembic_head(engine)


def alembic_head_revision() -> str | None:
    """The latest revision id in ``backend/migrations/versions``.

    Read from the scripts rather than hard-coded, so adding a migration cannot
    leave this behind.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config_path = Path(__file__).resolve().parents[3] / "alembic.ini"
        if not config_path.exists():
            return None
        return ScriptDirectory.from_config(Config(str(config_path))).get_current_head()
    except Exception:  # noqa: BLE001 - stamping is a convenience, never a hard requirement
        logger.debug("could not resolve the Alembic head revision", exc_info=True)
        return None


def stamp_alembic_head(engine: Engine | None = None) -> str | None:
    """Mark an already-created schema as being at the latest revision.

    Writes the version row directly rather than going through
    ``alembic.command.stamp``, which would run ``env.py`` and build its own
    engine from ``DATABASE_URL`` — wrong for an in-memory test database, and
    wrong for any caller that passed a specific engine.

    Never raises: a database that works but is not stamped is a worse outcome
    than one that is stamped, but a database that refuses to start because
    bookkeeping failed is worse than both.
    """
    engine = engine or get_engine()
    revision = alembic_head_revision()
    if revision is None:
        return None

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version ("
                    "version_num VARCHAR(32) NOT NULL, "
                    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
                )
            )
            already = connection.execute(text("SELECT version_num FROM alembic_version")).first()
            if already is None:
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                    {"revision": revision},
                )
    except SQLAlchemyError:
        logger.warning("could not stamp the Alembic revision", exc_info=True)
        return None
    return revision


def drop_all(engine: Engine | None = None) -> None:
    """Drop the schema, including the Alembic bookkeeping.

    Leaving ``alembic_version`` behind would claim a revision for a database
    that no longer has any of its tables.
    """
    engine = engine or get_engine()
    Base.metadata.drop_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    except SQLAlchemyError:  # pragma: no cover - best effort teardown
        logger.debug("could not drop alembic_version", exc_info=True)


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
