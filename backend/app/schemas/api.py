"""Request and response shapes for the v1 API.

Ingestion reuses the domain model directly (``Trace``, ``Span``, ``Event``)
rather than defining parallel request schemas. A second definition of the wire
format is the thing D-004 exists to prevent, and it would let the SDK and the
API drift apart one field at a time.

Response schemas are their own types, because what a list endpoint returns is
genuinely not a trace: it is a summary shaped for a table.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from tracelens.models import Event, SpanStatus, Stage


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    #: False when the database is unreachable, so a load balancer can act on it.
    database_ok: bool


class IngestResponse(BaseModel):
    """What ingestion tells the caller it did.

    Includes the diagnosis when analysis ran, so a benchmark or a CI check can
    ingest and assert in one round trip.
    """

    trace_id: str
    spans_ingested: int
    events_ingested: int
    analysed: bool
    healthy: bool | None = None
    root_cause_stage: Stage | None = None
    diagnostic_confidence: float | None = None
    summary: str | None = None


class EventIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    span_id: str
    event: Event


class SpanSummary(BaseModel):
    """A span as the trace tree renders it."""

    span_id: str
    parent_span_id: str | None
    name: str
    stage: Stage
    status: SpanStatus
    start_time: datetime
    end_time: datetime | None
    duration_ms: float | None
    error_type: str | None = None
    error_message: str | None = None
    attributes: dict = Field(default_factory=dict)
    inputs: dict = Field(default_factory=dict)
    outputs: dict = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)


class TraceSummary(BaseModel):
    """A trace as the list view renders it: no payloads, just shape and verdict."""

    trace_id: str
    name: str
    project: str
    pipeline: str
    status: SpanStatus
    start_time: datetime
    end_time: datetime | None
    duration_ms: float | None
    span_count: int
    failed_span_count: int
    root_cause_stage: Stage | None = None
    diagnostic_confidence: float | None = None
    analysed: bool = False


class TraceListResponse(BaseModel):
    items: list[TraceSummary]
    total: int
    limit: int
    offset: int
    has_more: bool


class PipelineHealth(BaseModel):
    project: str
    pipeline: str
    total_traces: int
    failed_traces: int
    failure_rate: float
    average_latency_ms: float


class FailureBreakdownItem(BaseModel):
    category: str
    stage: str
    count: int


class OverviewResponse(BaseModel):
    """The dashboard's headline panel."""

    total_traces: int
    #: Traces where a span raised. What an APM would call the failure rate.
    failed_traces: int
    failure_rate: float
    #: Traces where the forensic engine found a divergence, whether or not
    #: anything raised. Usually the larger and more useful number.
    diagnosed_failure_rate: float
    root_causes_identified: int
    average_latency_ms: float
    top_failure_stages: list[dict]
    projects: list[str]


class ErrorResponse(BaseModel):
    """A predictable error body, so clients can branch on it."""

    detail: str
    error_type: str = "error"
