"""Phase 4 acceptance: persistence, mapping, and the aggregate queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.forensics import generate_root_cause_report
from app.services.ingest import analyse_trace, ingest_trace
from app.storage.models import EventRow, FailureRow, RootCauseReportRow, SpanRow, TraceRow
from app.storage.repository import TraceFilter
from factories import (
    CURRENT_POLICY,
    QUESTION,
    T0,
    TraceFactory,
    healthy_trace,
    retrieval_failure_trace,
    tool_timeout_trace,
)
from sqlalchemy import inspect, select
from tracelens.models import ErrorInfo, SpanStatus, Stage

# -- schema ---------------------------------------------------------------


def test_every_table_is_created(engine):
    assert set(inspect(engine).get_table_names()) == {
        "traces",
        "spans",
        "events",
        "failures",
        "evaluations",
        "root_cause_reports",
    }


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        (
            "traces",
            {
                "ix_traces_project_start",
                "ix_traces_pipeline_start",
                "ix_traces_status_start",
                "ix_traces_start_time",
            },
        ),
        ("spans", {"ix_spans_trace_sequence", "ix_spans_stage_status", "ix_spans_parent"}),
        ("failures", {"ix_failures_trace", "ix_failures_category_stage"}),
    ],
)
def test_the_indexes_the_dashboard_queries_need_exist(engine, table, expected):
    # Section 4 requires indexes on project, trace id, status, timestamp, and
    # stage. Each is the leading column of one of these.
    names = {index["name"] for index in inspect(engine).get_indexes(table)}
    assert expected <= names


# -- round trips ----------------------------------------------------------


def test_a_trace_round_trips_through_the_database(repo, session):
    original = retrieval_failure_trace()
    repo.save_trace(original)
    session.commit()

    restored = repo.get_trace(original.trace_id)

    assert restored.name == original.name
    assert restored.project == original.project
    assert restored.status is original.status
    assert [s.name for s in restored.spans] == [s.name for s in original.spans]
    assert [s.stage for s in restored.spans] == [s.stage for s in original.spans]
    assert restored.spans[1].outputs == original.spans[1].outputs
    assert restored.spans[1].inputs == original.spans[1].inputs


def test_timestamps_survive_the_round_trip(repo, session):
    original = healthy_trace()
    repo.save_trace(original)
    session.commit()

    restored = repo.get_trace(original.trace_id)

    # SQLite has no timezone type, so this proves the naive-is-UTC rule in the
    # model actually holds end to end rather than only in memory.
    assert restored.start_time == original.start_time
    assert restored.spans[0].start_time == original.spans[0].start_time
    assert restored.spans[0].duration_ms == original.spans[0].duration_ms


def test_errors_survive_the_round_trip(repo, session):
    original = tool_timeout_trace()
    repo.save_trace(original)
    session.commit()

    restored = repo.get_trace(original.trace_id)
    failed = next(s for s in restored.spans if s.failed)

    assert failed.error.type == "TimeoutError"
    assert "2000ms" in failed.error.message
    assert failed.status is SpanStatus.ERROR


def test_events_survive_the_round_trip(repo, session):
    factory = TraceFactory()
    span = factory.add("retriever", Stage.RETRIEVAL, outputs={"documents": []})
    span.record_event("cache_miss", key="q")
    span.record_event("fallback", used=True)
    original = factory.finish()

    repo.save_trace(original)
    session.commit()
    restored = repo.get_trace(original.trace_id)

    assert [e.name for e in restored.spans[0].events] == ["cache_miss", "fallback"]
    assert restored.spans[0].events[0].attributes == {"key": "q"}


def test_span_order_is_preserved_when_start_times_tie(repo, session):
    # Two spans starting at the same instant must come back in the order they
    # were recorded; an ORDER BY start_time alone would lose that.
    factory = TraceFactory()
    for name in ("first", "second", "third"):
        factory.add(name, Stage.OTHER, duration_s=0.0, gap_s=0.0)
    for span in factory.trace.spans:
        span.start_time = T0
        span.end_time = T0
    original = factory.finish()

    repo.save_trace(original)
    session.commit()

    assert [s.name for s in repo.get_trace(original.trace_id).spans] == [
        "first",
        "second",
        "third",
    ]


def test_nested_parent_links_survive(repo, session):
    factory = TraceFactory()
    parent = factory.add("pipeline", Stage.OTHER, duration_s=1.0)
    child = factory.add("retriever", Stage.RETRIEVAL, outputs={"documents": []})
    child.parent_span_id = parent.span_id
    original = factory.finish()

    repo.save_trace(original)
    session.commit()
    restored = repo.get_trace(original.trace_id)

    assert restored.children_of(parent.span_id)[0].name == "retriever"


def test_an_unknown_trace_reads_back_as_none(repo):
    assert repo.get_trace("f" * 32) is None


# -- overwrite and delete -------------------------------------------------


def test_saving_the_same_trace_twice_replaces_it(repo, session):
    trace = retrieval_failure_trace()
    repo.save_trace(trace)
    repo.save_trace(trace)
    session.commit()

    assert session.scalar(select(TraceRow.trace_id)) == trace.trace_id
    assert len(session.scalars(select(SpanRow)).all()) == len(trace.spans)


def test_deleting_a_trace_cascades_to_spans_events_and_findings(repo, session):
    trace = retrieval_failure_trace()
    trace.spans[0].record_event("started")
    ingest_trace(session, trace)
    session.commit()

    assert session.scalars(select(SpanRow)).all()
    assert session.scalars(select(FailureRow)).all()

    repo.delete_trace(trace.trace_id)
    session.commit()

    assert session.scalars(select(SpanRow)).all() == []
    assert session.scalars(select(EventRow)).all() == []
    assert session.scalars(select(FailureRow)).all() == []
    assert session.scalars(select(RootCauseReportRow)).all() == []


def test_deleting_an_absent_trace_reports_false(repo):
    assert repo.delete_trace("f" * 32) is False


# -- findings and reports -------------------------------------------------


def test_findings_are_stored_and_read_back_as_candidates(repo, session):
    trace = retrieval_failure_trace()
    ingest_trace(session, trace)
    session.commit()

    failures = repo.get_failures(trace.trace_id)

    assert len(failures) == 2
    assert all(f.evidence for f in failures)
    assert {f.stage for f in failures} == {Stage.RETRIEVAL}


def test_re_analysis_replaces_findings_rather_than_appending(repo, session):
    trace = retrieval_failure_trace()
    ingest_trace(session, trace)
    session.commit()
    before = len(repo.get_failures(trace.trace_id))

    analyse_trace(session, trace)
    session.commit()

    assert len(repo.get_failures(trace.trace_id)) == before


def test_a_report_round_trips_with_its_evidence(repo, session):
    trace = retrieval_failure_trace()
    repo.save_trace(trace)
    original = generate_root_cause_report(trace)
    repo.save_report(original)
    session.commit()

    restored = repo.get_report(trace.trace_id)

    assert restored.likely_root_cause.stage is Stage.RETRIEVAL
    assert restored.summary == original.summary
    assert len(restored.evidence_chain) == len(original.evidence_chain)
    assert (
        restored.divergence.first_divergence_span_id == original.divergence.first_divergence_span_id
    )


def test_report_columns_are_populated_for_querying(repo, session):
    trace = retrieval_failure_trace()
    ingest_trace(session, trace)
    session.commit()

    row = session.get(RootCauseReportRow, trace.trace_id)

    assert row.root_cause_stage == "retrieval"
    assert row.healthy == 0
    assert row.confidence > 0
    assert row.analysis_ms > 0


def test_an_unanalysed_trace_has_no_report(repo, session):
    trace = healthy_trace()
    ingest_trace(session, trace, analyse=False)
    session.commit()

    assert repo.get_report(trace.trace_id) is None
    assert repo.get_failures(trace.trace_id) == []


# -- filtering ------------------------------------------------------------


@pytest.fixture
def mixed_traces(session):
    for index in range(6):
        factory = TraceFactory(
            f"run-{index}",
            project="alpha" if index % 2 == 0 else "beta",
            started_at=T0 + timedelta(minutes=index),
        )
        factory.add(
            "retriever",
            Stage.RETRIEVAL,
            inputs={"query": QUESTION},
            outputs={"documents": [CURRENT_POLICY]},
        )
        if index == 5:
            factory.add("tool", Stage.TOOL, error=ErrorInfo(type="TimeoutError"))
        ingest_trace(session, factory.finish(), analyse=False)
    session.commit()


def test_filtering_by_project(repo, mixed_traces):
    assert repo.list_traces(TraceFilter(project="alpha")).total == 3


def test_filtering_by_status(repo, mixed_traces):
    assert repo.list_traces(TraceFilter(status="error")).total == 1
    assert repo.list_traces(TraceFilter(failed_only=True)).total == 1


def test_filtering_by_stage_uses_a_subquery(repo, mixed_traces):
    assert repo.list_traces(TraceFilter(stage=Stage.TOOL)).total == 1
    assert repo.list_traces(TraceFilter(stage=Stage.RETRIEVAL)).total == 6


def test_filtering_by_time_window(repo, mixed_traces):
    since = T0 + timedelta(minutes=4)
    assert repo.list_traces(TraceFilter(since=since)).total == 2
    assert repo.list_traces(TraceFilter(until=since)).total == 5


def test_filters_combine(repo, mixed_traces):
    assert repo.list_traces(TraceFilter(project="alpha", stage=Stage.TOOL)).total == 0
    assert repo.list_traces(TraceFilter(project="beta", stage=Stage.TOOL)).total == 1


def test_paging_reports_totals_independent_of_the_page(repo, mixed_traces):
    page = repo.list_traces(limit=2, offset=2)

    assert page.total == 6
    assert len(page.items) == 2
    assert page.has_more is True


# -- aggregates -----------------------------------------------------------


def test_overview_counts_and_rates(repo, session):
    ingest_trace(session, retrieval_failure_trace())
    ingest_trace(session, healthy_trace())
    ingest_trace(session, tool_timeout_trace())
    session.commit()

    body = repo.overview()

    assert body["total_traces"] == 3
    assert body["failed_traces"] == 1  # only the tool timeout actually raised
    assert body["root_causes_identified"] == 2
    assert body["diagnosed_failure_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert body["average_latency_ms"] > 0


def test_overview_of_an_empty_database(repo):
    body = repo.overview()

    assert body == {
        "total_traces": 0,
        "failed_traces": 0,
        "failure_rate": 0.0,
        "diagnosed_failure_rate": 0.0,
        "root_causes_identified": 0,
        "average_latency_ms": 0.0,
        "top_failure_stages": [],
        "projects": [],
    }


def test_overview_can_be_scoped_to_a_project(repo, mixed_traces):
    assert repo.overview(project="alpha")["total_traces"] == 3


def test_pipeline_health_groups_and_averages(repo, mixed_traces):
    rows = repo.pipeline_health()

    assert {r["project"] for r in rows} == {"alpha", "beta"}
    assert sum(r["total_traces"] for r in rows) == 6
    beta = next(r for r in rows if r["project"] == "beta")
    assert beta["failed_traces"] == 1
    assert beta["failure_rate"] == pytest.approx(1 / 3, abs=1e-3)


def test_failure_breakdown_groups_by_category_and_stage(repo, session):
    ingest_trace(session, retrieval_failure_trace())
    ingest_trace(session, tool_timeout_trace())
    session.commit()

    rows = repo.failure_breakdown()
    by_category = {r["category"]: r["count"] for r in rows}

    assert by_category["retrieval_failure"] == 2
    assert by_category["execution_error"] == 1


def test_distinct_projects_are_sorted(repo, mixed_traces):
    assert repo.distinct_projects() == ["alpha", "beta"]


# -- ingest service -------------------------------------------------------


def test_ingest_returns_a_report_when_analysis_is_on(session):
    result = ingest_trace(session, retrieval_failure_trace())

    assert result.analysed is True
    assert result.healthy is False
    assert result.report.likely_root_cause.stage is Stage.RETRIEVAL
    assert result.spans_ingested == 4


def test_ingest_can_skip_analysis(session):
    result = ingest_trace(session, retrieval_failure_trace(), analyse=False)

    assert result.analysed is False
    assert result.report is None
    assert result.healthy is None


def test_analysis_latency_is_measured_not_asserted(session):
    result = ingest_trace(session, retrieval_failure_trace())

    # Section 32: only claim performance that was measured.
    assert 0.0 < result.report.analysis_ms < 500.0


def test_ingested_at_is_recorded(session):
    trace = healthy_trace()
    ingest_trace(session, trace, analyse=False)
    session.commit()

    row = session.get(TraceRow, trace.trace_id)
    assert row.ingested_at is not None
    assert row.ingested_at.replace(tzinfo=UTC) <= datetime.now(UTC)
