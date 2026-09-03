"""Ingestion and analysis, the two things the API actually does.

Analysis runs inline with ingestion (D-003). One trace is a bounded in-memory
pass costing single-digit milliseconds, measured and reported on every report
as ``analysis_ms``, so a queue would add a failure mode and an operational
component to save nothing yet. When that number stops being small, the
measurement will say so and the decision can be revisited on evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session
from tracelens.models import Event, Span, Trace, utcnow

from ..core.config import get_settings
from ..detection import DetectionConfig
from ..forensics import RootCauseReport, generate_root_cause_report
from ..invariants import InvariantRegistry
from ..storage.models import EventRow, SpanRow, TraceRow
from ..storage.repository import TraceRepository


@dataclass
class IngestResult:
    trace_id: str
    spans_ingested: int
    events_ingested: int
    analysed: bool
    report: RootCauseReport | None = None

    @property
    def healthy(self) -> bool | None:
        return self.report.healthy if self.report else None


def ingest_trace(
    session: Session,
    trace: Trace,
    analyse: bool | None = None,
    config: DetectionConfig | None = None,
    registry: InvariantRegistry | None = None,
) -> IngestResult:
    """Store a trace and, unless told otherwise, diagnose it immediately."""
    repository = TraceRepository(session)
    repository.save_trace(trace)

    events = sum(len(span.events) for span in trace.spans)
    should_analyse = get_settings().analyse_on_ingest if analyse is None else analyse

    report = None
    if should_analyse:
        report = analyse_trace(session, trace, config=config, registry=registry)

    return IngestResult(
        trace_id=trace.trace_id,
        spans_ingested=len(trace.spans),
        events_ingested=events,
        analysed=report is not None,
        report=report,
    )


def analyse_trace(
    session: Session,
    trace: Trace,
    config: DetectionConfig | None = None,
    registry: InvariantRegistry | None = None,
) -> RootCauseReport:
    """Run the forensic pass and persist its findings alongside the trace."""
    repository = TraceRepository(session)
    report = generate_root_cause_report(trace, config=config, registry=registry)

    repository.save_failures(trace.trace_id, report.divergence.all_candidates)
    repository.save_report(report)
    return report


def reanalyse_trace(
    session: Session,
    trace_id: str,
    config: DetectionConfig | None = None,
    registry: InvariantRegistry | None = None,
) -> RootCauseReport | None:
    """Re-run analysis on a stored trace.

    Needed whenever the engine changes: a diagnosis produced by an older
    detector set should be replaceable without re-running the pipeline that
    produced the trace.
    """
    trace = TraceRepository(session).get_trace(trace_id)
    if trace is None:
        return None
    return analyse_trace(session, trace, config=config, registry=registry)


def ingest_span(session: Session, span: Span) -> SpanRow:
    """Add a single span to an existing trace.

    The streaming path, for a long-running pipeline that exports as it goes
    rather than at the end. The trace must already exist; a span for an unknown
    trace is an error rather than a reason to invent one, because a
    manufactured trace would have no name, project, or start time and would
    corrupt every aggregate that reads those.
    """
    from ..storage.mapping import span_to_row

    trace = session.get(TraceRow, span.trace_id)
    if trace is None:
        raise LookupError(f"trace {span.trace_id} has not been ingested")

    existing = session.get(SpanRow, span.span_id)
    if existing is not None:
        session.delete(existing)
        session.flush()

    next_sequence = max((s.sequence for s in trace.spans), default=-1) + 1
    row = span_to_row(span, next_sequence)
    session.add(row)
    session.flush()
    return row


def ingest_event(session: Session, trace_id: str, span_id: str, event: Event) -> EventRow:
    """Attach a single event to an existing span."""
    span = session.get(SpanRow, span_id)
    if span is None or span.trace_id != trace_id:
        raise LookupError(f"span {span_id} does not belong to trace {trace_id}")

    row = EventRow(
        span_id=span_id,
        trace_id=trace_id,
        name=event.name,
        timestamp=event.timestamp or utcnow(),
        attributes=event.attributes,
    )
    session.add(row)
    session.flush()
    return row
