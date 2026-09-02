from __future__ import annotations

import pytest
import tracelens
from tracelens import MemoryExporter, Tracer
from tracelens.tracing import context


@pytest.fixture(autouse=True)
def clean_context():
    """No test may inherit a half-open trace from another."""
    context.clear()
    yield
    context.clear()


@pytest.fixture
def exporter() -> MemoryExporter:
    return MemoryExporter()


@pytest.fixture
def tracer(exporter: MemoryExporter) -> Tracer:
    # strict=True so a test never passes because an instrumentation error was
    # quietly logged instead of raised.
    return Tracer(project="test", pipeline="rag", exporter=exporter, strict=True)


@pytest.fixture
def default_tracer(exporter: MemoryExporter):
    """Point the module-level API at a fresh exporter, then restore it."""
    original = tracelens.get_tracer()
    saved = (
        original.project,
        original.pipeline,
        original.exporter,
        original.capture,
        original.strict,
    )
    tracelens.configure(project="test", pipeline="rag", exporter=exporter, strict=True)
    yield exporter
    (
        original.project,
        original.pipeline,
        original.exporter,
        original.capture,
        original.strict,
    ) = saved
