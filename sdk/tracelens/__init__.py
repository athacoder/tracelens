"""TraceLens: failure forensics for multi-stage AI pipelines.

The whole instrumentation surface is four names::

    from tracelens import trace, configure, record_event

    configure(project="support", exporter=HttpExporter())

    with trace("customer-support"):          # opens a trace
        with trace("retriever", stage=Stage.RETRIEVAL):   # opens a span
            record_event("cache_miss")

The same ``trace`` works as a decorator::

    @trace("retriever", stage=Stage.RETRIEVAL)
    def retrieve(query): ...

For explicit control, build a :class:`Tracer` yourself instead of using the
module-level default.
"""

from __future__ import annotations

import os
from typing import Any

from .client import TraceLensClient
from .exporters import Exporter, FileExporter, HttpExporter, MemoryExporter
from .ids import deterministic_ids, new_span_id, new_trace_id
from .models import (
    CaptureMode,
    ErrorInfo,
    Event,
    Severity,
    Span,
    SpanStatus,
    Stage,
    Trace,
)
from .redaction import redact
from .tracing import context
from .tracing.tracer import Scope, Tracer

__version__ = "0.1.0"

_default_tracer = Tracer()


def get_tracer() -> Tracer:
    """The module-level tracer used by the bare functions below."""
    return _default_tracer


def configure(
    project: str | None = None,
    pipeline: str | None = None,
    exporter: Exporter | None = None,
    capture: CaptureMode | None = None,
    strict: bool | None = None,
) -> Tracer:
    """Update the module-level tracer in place.

    Mutating rather than replacing keeps any ``Tracer`` reference a caller
    already holds pointed at the live configuration.
    """
    if project is not None:
        _default_tracer.project = project
    if pipeline is not None:
        _default_tracer.pipeline = pipeline
    if exporter is not None:
        _default_tracer.exporter = exporter
    if capture is not None:
        _default_tracer.capture = capture
    if strict is not None:
        _default_tracer.strict = strict
    return _default_tracer


def configure_from_env() -> Tracer:
    """Configure from the variables documented in ``.env.example``.

    ``TRACELENS_API_URL`` selects the HTTP exporter; without it the tracer
    keeps traces in memory, so importing TraceLens never makes network calls
    the caller did not ask for.
    """
    capture = os.getenv("TRACELENS_CAPTURE_MODE")
    api_url = os.getenv("TRACELENS_API_URL")
    return configure(
        project=os.getenv("TRACELENS_PROJECT"),
        pipeline=os.getenv("TRACELENS_PIPELINE"),
        exporter=HttpExporter(base_url=api_url) if api_url else None,
        capture=CaptureMode(capture) if capture else None,
    )


def trace(name: str | None = None, stage: Stage = Stage.OTHER, **attributes: Any) -> Scope:
    """Open a trace, or a span if a trace is already running.

    Usable as a context manager or as a decorator.
    """
    return Scope(_default_tracer, name, stage, attributes)


def span(name: str | None = None, stage: Stage = Stage.OTHER, **attributes: Any) -> Scope:
    """Alias of :func:`trace`, for call sites where "span" reads better."""
    return Scope(_default_tracer, name, stage, attributes)


def record_event(name: str, **attributes: Any) -> Event | None:
    return _default_tracer.record_event(name, **attributes)


def set_span_status(status: SpanStatus) -> Span | None:
    return _default_tracer.set_span_status(status)


def attach_error(error: BaseException) -> Span | None:
    return _default_tracer.attach_error(error)


def set_inputs(**inputs: Any) -> None:
    _default_tracer.set_inputs(**inputs)


def set_outputs(**outputs: Any) -> None:
    _default_tracer.set_outputs(**outputs)


def set_attributes(**attributes: Any) -> None:
    _default_tracer.set_attributes(**attributes)


def current_trace() -> Trace | None:
    return context.current_trace()


def current_span() -> Span | None:
    return context.current_span()


__all__ = [
    "CaptureMode",
    "ErrorInfo",
    "Event",
    "Exporter",
    "FileExporter",
    "HttpExporter",
    "MemoryExporter",
    "Scope",
    "Severity",
    "Span",
    "SpanStatus",
    "Stage",
    "Trace",
    "TraceLensClient",
    "Tracer",
    "attach_error",
    "configure",
    "configure_from_env",
    "context",
    "current_span",
    "current_trace",
    "deterministic_ids",
    "get_tracer",
    "new_span_id",
    "new_trace_id",
    "record_event",
    "redact",
    "set_attributes",
    "set_inputs",
    "set_outputs",
    "set_span_status",
    "span",
    "trace",
]
