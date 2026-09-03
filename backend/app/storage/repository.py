"""Query and persistence operations.

Everything the API does to the database goes through here, so the endpoints
stay about HTTP and the aggregate queries the dashboard needs live in one
place where their indexes can be reasoned about.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload
from tracelens.models import Stage, Trace

from ..detection.models import FailureCandidate
from ..evaluation.evaluators import EvaluationResult
from ..forensics.report import RootCauseReport
from .mapping import (
    candidate_to_row,
    evaluation_to_row,
    report_to_row,
    row_to_candidate,
    row_to_report,
    row_to_trace,
    trace_to_row,
)
from .models import EventRow, FailureRow, RootCauseReportRow, SpanRow, TraceRow


@dataclass(frozen=True)
class TraceFilter:
    """The filters the trace list supports."""

    project: str | None = None
    pipeline: str | None = None
    status: str | None = None
    stage: Stage | None = None
    failed_only: bool = False
    since: datetime | None = None
    until: datetime | None = None
    search: str | None = None


@dataclass(frozen=True)
class Page:
    """One page of results, with the total so a UI can show real pagination."""

    items: list[Any]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class TraceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- writes ----------------------------------------------------------

    def save_trace(self, trace: Trace) -> TraceRow:
        """Store a trace, replacing any previous version of it.

        Re-ingesting the same trace_id overwrites rather than erroring: a
        retried export must not fail, and a trace is immutable in practice, so
        the second copy is the same data.
        """
        existing = self.session.get(TraceRow, trace.trace_id)
        if existing is not None:
            self.session.delete(existing)
            self.session.flush()

        row = trace_to_row(trace)
        self.session.add(row)
        self.session.flush()
        return row

    def save_failures(self, trace_id: str, candidates: list[FailureCandidate]) -> None:
        self.session.query(FailureRow).filter(FailureRow.trace_id == trace_id).delete()
        for candidate in candidates:
            self.session.add(candidate_to_row(candidate, trace_id))
        self.session.flush()

    def save_evaluations(
        self,
        trace_id: str,
        results: list[EvaluationResult],
        span_id: str | None = None,
    ) -> None:
        for result in results:
            self.session.add(evaluation_to_row(result, trace_id, span_id))
        self.session.flush()

    def save_report(self, report: RootCauseReport) -> None:
        existing = self.session.get(RootCauseReportRow, report.trace_id)
        if existing is not None:
            self.session.delete(existing)
            self.session.flush()
        self.session.add(report_to_row(report))
        self.session.flush()

    def delete_trace(self, trace_id: str) -> bool:
        row = self.session.get(TraceRow, trace_id)
        if row is None:
            return False
        self.session.delete(row)
        return True

    # -- reads -----------------------------------------------------------

    def get_trace(self, trace_id: str) -> Trace | None:
        row = self._trace_row(trace_id)
        return row_to_trace(row) if row is not None else None

    def _trace_row(self, trace_id: str) -> TraceRow | None:
        return self.session.scalars(
            select(TraceRow)
            .where(TraceRow.trace_id == trace_id)
            .options(selectinload(TraceRow.spans).selectinload(SpanRow.events))
        ).one_or_none()

    def get_spans(self, trace_id: str) -> list[SpanRow]:
        return list(
            self.session.scalars(
                select(SpanRow)
                .where(SpanRow.trace_id == trace_id)
                .order_by(SpanRow.sequence)
                .options(selectinload(SpanRow.events))
            )
        )

    def get_events(self, trace_id: str) -> list[EventRow]:
        return list(
            self.session.scalars(
                select(EventRow).where(EventRow.trace_id == trace_id).order_by(EventRow.timestamp)
            )
        )

    def list_traces(
        self,
        filters: TraceFilter | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> Page:
        """Newest first, which is what anyone debugging an incident wants."""
        filters = filters or TraceFilter()
        conditions = self._conditions(filters)

        total = self.session.scalar(select(func.count()).select_from(TraceRow).where(*conditions))
        rows = list(
            self.session.scalars(
                select(TraceRow)
                .where(*conditions)
                .order_by(TraceRow.start_time.desc(), TraceRow.trace_id)
                .limit(limit)
                .offset(offset)
                .options(selectinload(TraceRow.spans))
            )
        )
        return Page(items=rows, total=total or 0, limit=limit, offset=offset)

    def _conditions(self, filters: TraceFilter) -> list:
        conditions = []
        if filters.project:
            conditions.append(TraceRow.project == filters.project)
        if filters.pipeline:
            conditions.append(TraceRow.pipeline == filters.pipeline)
        if filters.status:
            conditions.append(TraceRow.status == filters.status)
        if filters.failed_only:
            conditions.append(TraceRow.status == "error")
        if filters.since:
            conditions.append(TraceRow.start_time >= filters.since)
        if filters.until:
            conditions.append(TraceRow.start_time <= filters.until)
        if filters.search:
            conditions.append(TraceRow.name.ilike(f"%{filters.search}%"))
        if filters.stage:
            conditions.append(
                TraceRow.trace_id.in_(
                    select(SpanRow.trace_id).where(SpanRow.stage == filters.stage.value)
                )
            )
        return conditions

    def get_failures(self, trace_id: str) -> list[FailureCandidate]:
        rows = self.session.scalars(
            select(FailureRow).where(FailureRow.trace_id == trace_id).order_by(FailureRow.id)
        )
        return [row_to_candidate(row) for row in rows]

    def get_report(self, trace_id: str) -> RootCauseReport | None:
        row = self.session.get(RootCauseReportRow, trace_id)
        return row_to_report(row) if row is not None else None

    # -- aggregates ------------------------------------------------------

    def overview(self, project: str | None = None) -> dict[str, Any]:
        """The dashboard's headline numbers, in four queries rather than N.

        ``projects`` and ``pipelines`` are derived here by grouping rather than
        being their own tables (D-009): neither has an attribute beyond its
        name yet, so a table would be a join for nothing.
        """
        conditions = [TraceRow.project == project] if project else []

        total = (
            self.session.scalar(select(func.count()).select_from(TraceRow).where(*conditions)) or 0
        )
        failed = (
            self.session.scalar(
                select(func.count())
                .select_from(TraceRow)
                .where(*conditions, TraceRow.status == "error")
            )
            or 0
        )
        average_latency = self.session.scalar(
            select(func.avg(TraceRow.duration_ms)).where(*conditions)
        )
        diagnosed = (
            self.session.scalar(
                select(func.count())
                .select_from(RootCauseReportRow)
                .join(TraceRow, TraceRow.trace_id == RootCauseReportRow.trace_id)
                .where(*conditions, RootCauseReportRow.healthy == 0)
            )
            or 0
        )

        top_stages = self.session.execute(
            select(RootCauseReportRow.root_cause_stage, func.count().label("n"))
            .join(TraceRow, TraceRow.trace_id == RootCauseReportRow.trace_id)
            .where(*conditions, RootCauseReportRow.root_cause_stage.is_not(None))
            .group_by(RootCauseReportRow.root_cause_stage)
            .order_by(func.count().desc())
            .limit(5)
        ).all()

        return {
            "total_traces": total,
            "failed_traces": failed,
            "failure_rate": round(failed / total, 4) if total else 0.0,
            # The number that distinguishes TraceLens from an APM. A trace
            # where the retriever returned a superseded document raises no
            # exception and reports status ok, so execution-failure rate says
            # the pipeline is healthy while the user got a wrong answer.
            "diagnosed_failure_rate": round(diagnosed / total, 4) if total else 0.0,
            "root_causes_identified": diagnosed,
            "average_latency_ms": round(average_latency, 2) if average_latency else 0.0,
            "top_failure_stages": [{"stage": stage, "count": count} for stage, count in top_stages],
            "projects": self.distinct_projects(),
        }

    def distinct_projects(self) -> list[str]:
        return list(
            self.session.scalars(select(TraceRow.project).distinct().order_by(TraceRow.project))
        )

    def pipeline_health(self, project: str | None = None) -> list[dict[str, Any]]:
        """Per-pipeline totals, failure rate, and mean latency."""
        conditions = [TraceRow.project == project] if project else []
        rows = self.session.execute(
            select(
                TraceRow.project,
                TraceRow.pipeline,
                func.count().label("total"),
                func.sum(case((TraceRow.status == "error", 1), else_=0)).label("failed"),
                func.avg(TraceRow.duration_ms).label("avg_ms"),
            )
            .where(*conditions)
            .group_by(TraceRow.project, TraceRow.pipeline)
            .order_by(TraceRow.project, TraceRow.pipeline)
        ).all()

        return [
            {
                "project": project_name,
                "pipeline": pipeline_name,
                "total_traces": total,
                "failed_traces": int(failed or 0),
                "failure_rate": round((failed or 0) / total, 4) if total else 0.0,
                "average_latency_ms": round(avg_ms, 2) if avg_ms else 0.0,
            }
            for project_name, pipeline_name, total, failed, avg_ms in rows
        ]

    def failure_breakdown(self, project: str | None = None) -> list[dict[str, Any]]:
        """Counts by failure category, for the failures screen."""
        conditions = [TraceRow.project == project] if project else []
        rows = self.session.execute(
            select(FailureRow.category, FailureRow.stage, func.count().label("n"))
            .join(TraceRow, TraceRow.trace_id == FailureRow.trace_id)
            .where(*conditions)
            .group_by(FailureRow.category, FailureRow.stage)
            .order_by(func.count().desc())
        ).all()
        return [
            {"category": category, "stage": stage, "count": count}
            for category, stage, count in rows
        ]
