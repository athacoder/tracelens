"""TraceLens: failure forensics for multi-stage AI pipelines."""

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

__version__ = "0.1.0"

__all__ = [
    "CaptureMode",
    "ErrorInfo",
    "Event",
    "Severity",
    "Span",
    "SpanStatus",
    "Stage",
    "Trace",
    "deterministic_ids",
    "new_span_id",
    "new_trace_id",
]
