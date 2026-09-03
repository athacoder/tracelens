"""SQLAlchemy tables.

These are rows, not the domain model. The domain model lives in
``tracelens.models`` and is the single definition of what a trace is (D-004);
this module exists only to put one in a database and get it back out.

Keeping them separate costs one mapping module and buys two things: the wire
format cannot drift from what the SDK produces, and the storage schema can be
indexed and denormalised for queries without that leaking into the contract.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TraceRow(Base):
    """One pipeline run."""

    __tablename__ = "traces"

    trace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    project: Mapped[str] = mapped_column(String(255), default="default")
    pipeline: Mapped[str] = mapped_column(String(255), default="default")
    status: Mapped[str] = mapped_column(String(16), default="unset")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    spans: Mapped[list[SpanRow]] = relationship(
        back_populates="trace", cascade="all, delete-orphan", order_by="SpanRow.sequence"
    )
    failures: Mapped[list[FailureRow]] = relationship(
        back_populates="trace", cascade="all, delete-orphan"
    )
    evaluations: Mapped[list[EvaluationRow]] = relationship(
        back_populates="trace", cascade="all, delete-orphan"
    )
    report: Mapped[RootCauseReportRow | None] = relationship(
        back_populates="trace", cascade="all, delete-orphan", uselist=False
    )

    # The dashboard's common queries: recent traces for a project, failures for
    # a pipeline. Both filter then sort by time, so the indexes are composite.
    __table_args__ = (
        Index("ix_traces_project_start", "project", "start_time"),
        Index("ix_traces_pipeline_start", "pipeline", "start_time"),
        Index("ix_traces_status_start", "status", "start_time"),
        Index("ix_traces_start_time", "start_time"),
    )


class SpanRow(Base):
    """One stage of a run."""

    __tablename__ = "spans"

    span_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("traces.trace_id", ondelete="CASCADE")
    )
    parent_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    stage: Mapped[str] = mapped_column(String(32), default="other")
    status: Mapped[str] = mapped_column(String(16), default="unset")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_stacktrace: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Insertion order within the trace. Preserves the tie-break the domain
    #: model uses when two spans share a start time, which a plain ORDER BY on
    #: start_time would lose.
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    trace: Mapped[TraceRow] = relationship(back_populates="spans")
    events: Mapped[list[EventRow]] = relationship(
        back_populates="span", cascade="all, delete-orphan", order_by="EventRow.id"
    )

    __table_args__ = (
        Index("ix_spans_trace_sequence", "trace_id", "sequence"),
        Index("ix_spans_stage_status", "stage", "status"),
        Index("ix_spans_parent", "parent_span_id"),
    )


class EventRow(Base):
    """A timestamped occurrence inside a span."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    span_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("spans.span_id", ondelete="CASCADE")
    )
    trace_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)

    span: Mapped[SpanRow] = relationship(back_populates="events")


class FailureRow(Base):
    """One detector or invariant finding, persisted so it need not be recomputed."""

    __tablename__ = "failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("traces.trace_id", ondelete="CASCADE")
    )
    span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detector: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=list)

    trace: Mapped[TraceRow] = relationship(back_populates="failures")

    __table_args__ = (
        Index("ix_failures_trace", "trace_id"),
        Index("ix_failures_category_stage", "category", "stage"),
    )


class EvaluationRow(Base):
    """A quality measurement attached to a trace."""

    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("traces.trace_id", ondelete="CASCADE")
    )
    span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Integer)
    explanation: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

    trace: Mapped[TraceRow] = relationship(back_populates="evaluations")

    __table_args__ = (Index("ix_evaluations_trace_name", "trace_id", "name"),)


class RootCauseReportRow(Base):
    """The forensic verdict for a trace.

    The full report is kept as JSON because it is read whole and never queried
    by its internals; the columns beside it are the fields the dashboard
    filters and aggregates on.
    """

    __tablename__ = "root_cause_reports"

    trace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("traces.trace_id", ondelete="CASCADE"), primary_key=True
    )
    healthy: Mapped[bool] = mapped_column(Integer, default=0)
    root_cause_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    root_cause_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    diagnostic_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[str] = mapped_column(Text, default="")
    analysis_ms: Mapped[float] = mapped_column(Float, default=0.0)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    report: Mapped[dict] = mapped_column(JSON, default=dict)

    trace: Mapped[TraceRow] = relationship(back_populates="report")

    __table_args__ = (Index("ix_reports_stage_healthy", "root_cause_stage", "healthy"),)
