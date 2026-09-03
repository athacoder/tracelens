"""Translation between the domain model and the tables.

The one place that knows both shapes (D-004). Everything else works with
``tracelens.models`` objects on one side or SQLAlchemy rows on the other, and
never with both at once.
"""

from __future__ import annotations

from tracelens.models import (
    ErrorInfo,
    Event,
    Span,
    SpanStatus,
    Stage,
    Trace,
    utcnow,
)

from ..detection.models import Evidence, FailureCandidate, FailureCategory
from ..evaluation.evaluators import EvaluationResult
from ..forensics.report import RootCauseReport
from .models import EvaluationRow, EventRow, FailureRow, RootCauseReportRow, SpanRow, TraceRow


def trace_to_row(trace: Trace) -> TraceRow:
    """Build a full row graph for one trace: trace, spans, events."""
    row = TraceRow(
        trace_id=trace.trace_id,
        name=trace.name,
        project=trace.project,
        pipeline=trace.pipeline,
        status=trace.status.value,
        start_time=trace.start_time,
        end_time=trace.end_time,
        duration_ms=trace.duration_ms,
        attributes=trace.attributes,
        ingested_at=utcnow(),
    )
    # Persist the trace's own span order so the tie-break between spans that
    # share a start time survives the round trip.
    row.spans = [span_to_row(span, index) for index, span in enumerate(trace.spans)]
    return row


def span_to_row(span: Span, sequence: int = 0) -> SpanRow:
    row = SpanRow(
        span_id=span.span_id,
        trace_id=span.trace_id,
        parent_span_id=span.parent_span_id,
        name=span.name,
        stage=span.stage.value,
        status=span.status.value,
        start_time=span.start_time,
        end_time=span.end_time,
        duration_ms=span.duration_ms,
        error_type=span.error.type if span.error else None,
        error_message=span.error.message if span.error else None,
        error_stacktrace=span.error.stacktrace if span.error else None,
        attributes=span.attributes,
        inputs=span.inputs,
        outputs=span.outputs,
        sequence=sequence,
    )
    row.events = [event_to_row(event, span) for event in span.events]
    return row


def event_to_row(event: Event, span: Span) -> EventRow:
    return EventRow(
        span_id=span.span_id,
        trace_id=span.trace_id,
        name=event.name,
        timestamp=event.timestamp,
        attributes=event.attributes,
    )


def row_to_trace(row: TraceRow) -> Trace:
    """Rebuild the domain object from its rows.

    Constructed field by field rather than through ``add_span`` so a trace that
    was already stored can always be read back, even if a later version of the
    model would reject it. Refusing to return stored data is worse than
    returning data a validator would now question.
    """
    return Trace(
        trace_id=row.trace_id,
        name=row.name,
        project=row.project,
        pipeline=row.pipeline,
        status=SpanStatus(row.status),
        start_time=row.start_time,
        end_time=row.end_time,
        attributes=row.attributes or {},
        spans=[row_to_span(span) for span in sorted(row.spans, key=lambda s: s.sequence)],
    )


def row_to_span(row: SpanRow) -> Span:
    error = (
        ErrorInfo(
            type=row.error_type,
            message=row.error_message or "",
            stacktrace=row.error_stacktrace,
        )
        if row.error_type
        else None
    )
    return Span(
        span_id=row.span_id,
        trace_id=row.trace_id,
        parent_span_id=row.parent_span_id,
        name=row.name,
        stage=Stage(row.stage),
        status=SpanStatus(row.status),
        start_time=row.start_time,
        end_time=row.end_time,
        error=error,
        attributes=row.attributes or {},
        inputs=row.inputs or {},
        outputs=row.outputs or {},
        events=[
            Event(name=e.name, timestamp=e.timestamp, attributes=e.attributes or {})
            for e in row.events
        ],
    )


def candidate_to_row(candidate: FailureCandidate, trace_id: str) -> FailureRow:
    return FailureRow(
        trace_id=trace_id,
        span_id=candidate.span_id,
        detector=candidate.detector,
        category=candidate.category.value,
        stage=candidate.stage.value,
        severity=candidate.severity.value,
        confidence=candidate.confidence,
        summary=candidate.summary,
        evidence=[e.model_dump(mode="json") for e in candidate.evidence],
    )


def row_to_candidate(row: FailureRow) -> FailureCandidate:
    from tracelens.models import Severity

    return FailureCandidate(
        detector=row.detector,
        category=FailureCategory(row.category),
        severity=Severity(row.severity),
        confidence=row.confidence,
        summary=row.summary,
        span_id=row.span_id,
        stage=Stage(row.stage),
        evidence=[Evidence.model_validate(e) for e in (row.evidence or [])],
    )


def evaluation_to_row(
    result: EvaluationResult,
    trace_id: str,
    span_id: str | None = None,
) -> EvaluationRow:
    return EvaluationRow(
        trace_id=trace_id,
        span_id=span_id,
        name=result.name,
        score=result.score,
        threshold=result.threshold,
        passed=int(result.passed),
        explanation=result.explanation,
        detail=result.detail,
    )


def report_to_row(report: RootCauseReport) -> RootCauseReportRow:
    likely = report.likely_root_cause
    return RootCauseReportRow(
        trace_id=report.trace_id,
        healthy=int(report.healthy),
        root_cause_span_id=likely.span_id if likely else None,
        root_cause_stage=likely.stage.value if likely else None,
        diagnostic_score=likely.score if likely else 0.0,
        confidence=likely.confidence if likely else 0.0,
        summary=report.summary,
        analysis_ms=report.analysis_ms,
        generated_at=report.generated_at,
        report=report.model_dump(mode="json"),
    )


def row_to_report(row: RootCauseReportRow) -> RootCauseReport:
    return RootCauseReport.model_validate(row.report)
