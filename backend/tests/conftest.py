from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The factories module is imported by name from every forensic test.
sys.path.insert(0, str(Path(__file__).parent))

from factories import (  # noqa: E402
    healthy_trace,
    model_failure_trace,
    postprocessing_failure_trace,
    retrieval_failure_trace,
    tool_timeout_trace,
)


@pytest.fixture
def healthy():
    return healthy_trace()


@pytest.fixture
def retrieval_failure():
    return retrieval_failure_trace()


@pytest.fixture
def model_failure():
    return model_failure_trace()


@pytest.fixture
def postprocessing_failure():
    return postprocessing_failure_trace()


@pytest.fixture
def tool_timeout():
    return tool_timeout_trace()


@pytest.fixture
def engine():
    """A private in-memory database per test.

    StaticPool keeps every connection pointed at the same in-memory database;
    without it each connection would get its own empty one and the API would
    read back nothing it had just written.
    """
    from app.storage import database
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    database.configure(engine)
    database.create_all(engine)
    yield engine
    database.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(engine):
    from app.storage.database import get_session_factory

    with get_session_factory()() as session:
        yield session


@pytest.fixture
def repo(session):
    from app.storage.repository import TraceRepository

    return TraceRepository(session)


@pytest.fixture
def client(engine):
    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def ingest(client):
    """Post a trace and return the parsed ingest response."""

    def _ingest(trace):
        response = client.post(
            "/api/v1/traces",
            content=trace.model_dump_json(),
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 202, response.text
        return response.json()

    return _ingest
