"""The v1 HTTP API.

Endpoints stay thin: validate, delegate to a service or the repository, shape
a response. Anything that reasons about a trace belongs in ``forensics``, and
anything that queries belongs in ``storage.repository``, so both stay testable
without HTTP.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from tracelens.models import Span, SpanStatus, Stage, Trace

from ..core.config import get_settings
from ..detection.models import FailureCandidate
from ..forensics import RootCauseReport
from ..schemas.api import (
    EventIngestRequest,
    HealthResponse,
    IngestResponse,
    OverviewResponse,
    PipelineHealth,
    SpanSummary,
    TraceListResponse,
    TraceSummary,
)
from ..services.ingest import ingest_event, ingest_span, ingest_trace, reanalyse_trace
from ..storage.database import get_db
from ..storage.models import SpanRow, TraceRow
from ..storage.repository import TraceFilter, TraceRepository

router = APIRouter(prefix="/api/v1")

DbSession = Annotated[Session, Depends(get_db)]


def _repository(session: Session) -> TraceRepository:
    return TraceRepository(session)


def _require_trace(session: Session, trace_id: str) -> Trace:
    trace = _repository(session).get_trace(trace_id)
    if trace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"trace {trace_id} not found")
    return trace


# -- health ---------------------------------------------------------------


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health(session: DbSession) -> HealthResponse:
    """Liveness plus a real database round trip.

    A health check that does not touch the database reports healthy while
    every request fails, which is worse than having no health check.
    """
    from app import __version__

    settings = get_settings()
    try:
        session.execute(text("SELECT 1"))
        database_ok = True
    except SQLAlchemyError:
        database_ok = False

    return HealthResponse(
        status="ok" if database_ok else "degraded",
        version=__version__,
        database="sqlite" if settings.is_sqlite else "postgresql",
        database_ok=database_ok,
    )


# -- ingestion ------------------------------------------------------------


@router.post(
    "/traces",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["ingestion"],
)
def create_trace(trace: Trace, session: DbSession) -> IngestResponse:
    """Ingest a complete trace and diagnose it.

    202 rather than 201: the trace is stored, and the diagnosis attached to the
    response is the engine's current reading, which re-analysis may revise.
    """
    result = ingest_trace(session, trace)
    likely = result.report.likely_root_cause if result.report else None
    return IngestResponse(
        trace_id=result.trace_id,
        spans_ingested=result.spans_ingested,
        events_ingested=result.events_ingested,
        analysed=result.analysed,
        healthy=result.healthy,
        root_cause_stage=likely.stage if likely else None,
        diagnostic_confidence=likely.confidence if likely else None,
        summary=result.report.summary if result.report else None,
    )


@router.post("/spans", status_code=status.HTTP_202_ACCEPTED, tags=["ingestion"])
def create_span(span: Span, session: DbSession) -> dict[str, str]:
    """Append one span to an already-ingested trace."""
    try:
        row = ingest_span(session, span)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return {"span_id": row.span_id, "trace_id": row.trace_id}


@router.post("/events", status_code=status.HTTP_202_ACCEPTED, tags=["ingestion"])
def create_event(payload: EventIngestRequest, session: DbSession) -> dict[str, str]:
    """Append one event to an already-ingested span."""
    try:
        ingest_event(session, payload.trace_id, payload.span_id, payload.event)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return {"span_id": payload.span_id, "trace_id": payload.trace_id}


# -- reads ----------------------------------------------------------------


@router.get("/traces", response_model=TraceListResponse, tags=["traces"])
def list_traces(
    session: DbSession,
    project: str | None = None,
    pipeline: str | None = None,
    trace_status: Annotated[str | None, Query(alias="status")] = None,
    stage: Stage | None = None,
    failed_only: bool = False,
    since: datetime | None = None,
    until: datetime | None = None,
    search: str | None = None,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TraceListResponse:
    """Traces newest first, filtered and paginated."""
    repository = _repository(session)
    page = repository.list_traces(
        TraceFilter(
            project=project,
            pipeline=pipeline,
            status=trace_status,
            stage=stage,
            failed_only=failed_only,
            since=since,
            until=until,
            search=search,
        ),
        limit=limit,
        offset=offset,
    )

    # One extra query for the whole page rather than one per row.
    reports = _reports_for(session, [row.trace_id for row in page.items])

    return TraceListResponse(
        items=[_summarise(row, reports.get(row.trace_id)) for row in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        has_more=page.has_more,
    )


def _reports_for(session: Session, trace_ids: list[str]) -> dict[str, object]:
    if not trace_ids:
        return {}
    from sqlalchemy import select

    from ..storage.models import RootCauseReportRow

    rows = session.scalars(
        select(RootCauseReportRow).where(RootCauseReportRow.trace_id.in_(trace_ids))
    )
    return {row.trace_id: row for row in rows}


def _summarise(row: TraceRow, report: object | None) -> TraceSummary:
    return TraceSummary(
        trace_id=row.trace_id,
        name=row.name,
        project=row.project,
        pipeline=row.pipeline,
        status=SpanStatus(row.status),
        start_time=row.start_time,
        end_time=row.end_time,
        duration_ms=row.duration_ms,
        span_count=len(row.spans),
        failed_span_count=sum(1 for s in row.spans if s.status == "error" or s.error_type),
        root_cause_stage=getattr(report, "root_cause_stage", None),
        diagnostic_confidence=getattr(report, "confidence", None),
        analysed=report is not None,
    )


@router.get("/traces/{trace_id}", response_model=Trace, tags=["traces"])
def get_trace(trace_id: str, session: DbSession) -> Trace:
    """The full trace, spans and events included."""
    return _require_trace(session, trace_id)


@router.get("/traces/{trace_id}/spans", response_model=list[SpanSummary], tags=["traces"])
def get_trace_spans(trace_id: str, session: DbSession) -> list[SpanSummary]:
    _require_trace(session, trace_id)
    return [_span_summary(row) for row in _repository(session).get_spans(trace_id)]


def _span_summary(row: SpanRow) -> SpanSummary:
    from tracelens.models import Event

    return SpanSummary(
        span_id=row.span_id,
        parent_span_id=row.parent_span_id,
        name=row.name,
        stage=Stage(row.stage),
        status=SpanStatus(row.status),
        start_time=row.start_time,
        end_time=row.end_time,
        duration_ms=row.duration_ms,
        error_type=row.error_type,
        error_message=row.error_message,
        attributes=row.attributes or {},
        inputs=row.inputs or {},
        outputs=row.outputs or {},
        events=[
            Event(name=e.name, timestamp=e.timestamp, attributes=e.attributes or {})
            for e in row.events
        ],
    )


@router.get(
    "/traces/{trace_id}/failures",
    response_model=list[FailureCandidate],
    tags=["forensics"],
)
def get_failures(trace_id: str, session: DbSession) -> list[FailureCandidate]:
    """Every finding recorded for a trace, detectors and invariants alike."""
    _require_trace(session, trace_id)
    return _repository(session).get_failures(trace_id)


@router.get(
    "/traces/{trace_id}/root-cause",
    response_model=RootCauseReport,
    tags=["forensics"],
)
def get_root_cause_report(trace_id: str, session: DbSession) -> RootCauseReport:
    """The stored diagnosis, computed at ingest."""
    _require_trace(session, trace_id)
    report = _repository(session).get_report(trace_id)
    if report is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"trace {trace_id} has not been analysed; POST to /traces/{trace_id}/analyse",
        )
    return report


@router.post(
    "/traces/{trace_id}/analyse",
    response_model=RootCauseReport,
    tags=["forensics"],
)
def analyse(trace_id: str, session: DbSession) -> RootCauseReport:
    """Re-run the forensic pass over a stored trace.

    Exists so a diagnosis produced by an older detector set can be replaced
    without re-running the pipeline that produced the trace.
    """
    report = reanalyse_trace(session, trace_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"trace {trace_id} not found")
    return report


@router.delete("/traces/{trace_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["traces"])
def delete_trace(trace_id: str, session: DbSession) -> None:
    if not _repository(session).delete_trace(trace_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"trace {trace_id} not found")


# -- aggregates -----------------------------------------------------------


@router.get("/overview", response_model=OverviewResponse, tags=["dashboard"])
def overview(session: DbSession, project: str | None = None) -> OverviewResponse:
    return OverviewResponse(**_repository(session).overview(project))


@router.get("/pipelines/health", response_model=list[PipelineHealth], tags=["dashboard"])
def get_pipeline_health(session: DbSession, project: str | None = None) -> list[PipelineHealth]:
    return [PipelineHealth(**row) for row in _repository(session).pipeline_health(project)]


@router.get("/failures/breakdown", tags=["dashboard"])
def failure_breakdown(session: DbSession, project: str | None = None) -> list[dict]:
    """Finding counts by category and stage, for the failures screen."""
    return _repository(session).failure_breakdown(project)
