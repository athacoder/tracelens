"""The TraceLens domain model.

This module is the single definition of what a trace is (D-004). The backend
imports it for its API contract; the SDK produces it; the forensic engine
consumes it. Storage rows are a separate concern and live in the backend.

Vocabulary follows OpenTelemetry: a trace holds spans, spans nest through
``parent_span_id``, spans carry attributes, a status, and timestamped events.
The additions TraceLens needs on top of OTel are ``stage`` (which pipeline step
this span represents) and the ``inputs``/``outputs`` payloads that make
stage-to-stage invariant checking possible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .ids import is_valid_span_id, is_valid_trace_id, new_span_id, new_trace_id


def utcnow() -> datetime:
    return datetime.now(UTC)


class Stage(StrEnum):
    """The pipeline steps TraceLens knows how to reason about.

    Ordering is declaration order and is used only as a weak prior for
    dependency reasoning; the actual order of a trace comes from its spans.
    """

    PREPROCESSING = "preprocessing"
    DOCUMENT_LOAD = "document_load"
    CHUNKING = "chunking"
    RETRIEVAL = "retrieval"
    PROMPT_BUILD = "prompt_build"
    LLM = "llm"
    TOOL = "tool"
    POSTPROCESSING = "postprocessing"
    VALIDATION = "validation"
    OTHER = "other"

    @property
    def position(self) -> int:
        return list(Stage).index(self)


class SpanStatus(StrEnum):
    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def weight(self) -> float:
        return {
            "info": 0.1,
            "low": 0.3,
            "medium": 0.6,
            "high": 0.85,
            "critical": 1.0,
        }[self.value]


class CaptureMode(StrEnum):
    """How much payload the SDK records (see section 31 of CLAUDE.md)."""

    FULL = "full"
    REDACTED = "redacted"
    METADATA = "metadata"


class ErrorInfo(BaseModel):
    """An exception attached to a span."""

    model_config = ConfigDict(extra="forbid")

    type: str
    message: str = ""
    stacktrace: str | None = None

    def __str__(self) -> str:
        return f"{self.type}: {self.message}" if self.message else self.type


class Event(BaseModel):
    """A timestamped point occurrence inside a span."""

    model_config = ConfigDict(extra="forbid")

    name: str
    timestamp: datetime = Field(default_factory=utcnow)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _require_tz(cls, value: datetime) -> datetime:
        return _as_utc(value)

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("event name must not be empty")
        return value


class Span(BaseModel):
    """One unit of work in a pipeline run.

    A span is open while ``end_time`` is None. Validation here covers what can
    be judged from the span alone; anything needing sibling spans (a parent
    that does not exist, ordering against a parent) is checked by
    ``Trace.structural_errors``.
    """

    model_config = ConfigDict(extra="forbid")

    span_id: str = Field(default_factory=new_span_id)
    trace_id: str
    parent_span_id: str | None = None
    name: str
    stage: Stage = Stage.OTHER
    start_time: datetime = Field(default_factory=utcnow)
    end_time: datetime | None = None
    status: SpanStatus = SpanStatus.UNSET
    error: ErrorInfo | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)

    @field_validator("trace_id")
    @classmethod
    def _valid_trace_id(cls, value: str) -> str:
        if not is_valid_trace_id(value):
            raise ValueError(f"malformed trace_id: {value!r}")
        return value

    @field_validator("span_id", "parent_span_id")
    @classmethod
    def _valid_span_id(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_span_id(value):
            raise ValueError(f"malformed span_id: {value!r}")
        return value

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("span name must not be empty")
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def _require_tz(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _as_utc(value)

    @model_validator(mode="after")
    def _check_span(self) -> Span:
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError(
                f"span {self.span_id} ends before it starts "
                f"({self.end_time.isoformat()} < {self.start_time.isoformat()})"
            )
        if self.parent_span_id == self.span_id:
            raise ValueError(f"span {self.span_id} is its own parent")
        if self.error is not None and self.status is SpanStatus.OK:
            raise ValueError(f"span {self.span_id} has an error but status ok")
        return self

    @property
    def is_open(self) -> bool:
        return self.end_time is None

    @property
    def duration_ms(self) -> float | None:
        """Wall-clock duration, or None while the span is still open."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time).total_seconds() * 1000.0

    @property
    def failed(self) -> bool:
        return self.status is SpanStatus.ERROR or self.error is not None

    def record_event(self, name: str, **attributes: Any) -> Event:
        event = Event(name=name, attributes=dict(attributes))
        self.events.append(event)
        return event


class Trace(BaseModel):
    """A complete pipeline run.

    Spans are held in the order they were started. Duplicate span ids and
    parent cycles are always corruption and raise; a missing parent is reported
    by ``structural_errors`` instead, because during streaming ingestion a
    child can legitimately arrive before its parent.
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(default_factory=new_trace_id)
    name: str
    project: str = "default"
    pipeline: str = "default"
    start_time: datetime = Field(default_factory=utcnow)
    end_time: datetime | None = None
    status: SpanStatus = SpanStatus.UNSET
    attributes: dict[str, Any] = Field(default_factory=dict)
    spans: list[Span] = Field(default_factory=list)

    @field_validator("trace_id")
    @classmethod
    def _valid_trace_id(cls, value: str) -> str:
        if not is_valid_trace_id(value):
            raise ValueError(f"malformed trace_id: {value!r}")
        return value

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trace name must not be empty")
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def _require_tz(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _as_utc(value)

    @model_validator(mode="after")
    def _check_trace(self) -> Trace:
        self.raise_on_corruption()
        return self

    def raise_on_corruption(self) -> None:
        """Reject states that can never be legitimate.

        Kept as a plain method, not just a validator body, so ``add_span`` can
        re-run it after mutating an already-constructed trace.
        """
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("trace ends before it starts")

        seen: set[str] = set()
        for span in self.spans:
            if span.trace_id != self.trace_id:
                raise ValueError(
                    f"span {span.span_id} belongs to trace {span.trace_id}, not {self.trace_id}"
                )
            if span.span_id in seen:
                raise ValueError(f"duplicate span_id {span.span_id}")
            seen.add(span.span_id)

        self._reject_cycles()

    def _reject_cycles(self) -> None:
        parents = {s.span_id: s.parent_span_id for s in self.spans}
        for span_id in parents:
            seen = {span_id}
            cursor = parents[span_id]
            while cursor is not None and cursor in parents:
                if cursor in seen:
                    raise ValueError(f"parent cycle involving span {cursor}")
                seen.add(cursor)
                cursor = parents[cursor]

    # -- lookups ---------------------------------------------------------

    @property
    def duration_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time).total_seconds() * 1000.0

    @property
    def failed(self) -> bool:
        return self.status is SpanStatus.ERROR or any(s.failed for s in self.spans)

    def span(self, span_id: str) -> Span | None:
        return next((s for s in self.spans if s.span_id == span_id), None)

    def children_of(self, span_id: str | None) -> list[Span]:
        return [s for s in self.spans if s.parent_span_id == span_id]

    @property
    def root_spans(self) -> list[Span]:
        """Spans with no parent, or whose parent is absent from this trace."""
        known = {s.span_id for s in self.spans}
        return [s for s in self.spans if s.parent_span_id is None or s.parent_span_id not in known]

    def ordered_spans(self) -> list[Span]:
        """Spans in execution order: by start time, ties broken by insertion.

        This is the order the forensic engine walks, so "earliest divergence"
        means earliest in this ordering.
        """
        indexes = {id(s): i for i, s in enumerate(self.spans)}
        return sorted(self.spans, key=lambda s: (s.start_time, indexes[id(s)]))

    def stage_spans(self, stage: Stage) -> list[Span]:
        return [s for s in self.spans if s.stage is stage]

    def structural_errors(self) -> list[str]:
        """Non-fatal integrity problems: worth surfacing, not worth refusing.

        Kept separate from the raising validators so a partially ingested or
        deliberately damaged trace can still be stored and analysed.
        """
        problems: list[str] = []
        known = {s.span_id for s in self.spans}
        for span in self.spans:
            if span.parent_span_id is not None and span.parent_span_id not in known:
                problems.append(
                    f"span {span.span_id} ({span.name}) references missing parent "
                    f"{span.parent_span_id}"
                )
                continue
            parent = self.span(span.parent_span_id) if span.parent_span_id else None
            if parent is None:
                continue
            if span.start_time < parent.start_time:
                problems.append(
                    f"span {span.span_id} ({span.name}) starts before its parent "
                    f"{parent.span_id} ({parent.name})"
                )
            if (
                parent.end_time is not None
                and span.end_time is not None
                and span.end_time > parent.end_time
            ):
                problems.append(
                    f"span {span.span_id} ({span.name}) ends after its parent "
                    f"{parent.span_id} ({parent.name})"
                )
        for span in self.spans:
            if span.is_open and self.end_time is not None:
                problems.append(f"span {span.span_id} ({span.name}) never ended")
        return problems

    def add_span(self, span: Span) -> Span:
        """Append a span, re-running trace-level validation."""
        self.spans.append(span)
        try:
            self.raise_on_corruption()
        except ValueError:
            self.spans.pop()
            raise
        return span


def _as_utc(value: datetime) -> datetime:
    """Read naive datetimes as UTC rather than rejecting them.

    Rejecting them would make the SDK painful to use from code that calls
    ``datetime.now()``; guessing local time would make traces from different
    machines incomparable.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
