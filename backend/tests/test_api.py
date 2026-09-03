"""Phase 3 acceptance: the trace ingestion API."""

from __future__ import annotations

from datetime import timedelta

import pytest
from factories import QUESTION, T0, TraceFactory, healthy_trace, retrieval_failure_trace
from tracelens.models import Stage

# -- health ---------------------------------------------------------------


def test_health_reports_ok_and_touches_the_database(client):
    body = client.get("/api/v1/health").json()

    assert body["status"] == "ok"
    assert body["database_ok"] is True
    assert body["version"]


def test_root_points_at_the_docs(client):
    assert client.get("/").json()["docs"] == "/docs"


def test_the_openapi_schema_is_generated(client):
    schema = client.get("/openapi.json").json()

    assert "/api/v1/traces" in schema["paths"]
    assert "/api/v1/traces/{trace_id}/root-cause" in schema["paths"]


# -- ingestion happy path -------------------------------------------------


def test_ingesting_a_trace_stores_and_diagnoses_it(ingest):
    body = ingest(retrieval_failure_trace())

    assert body["spans_ingested"] == 4
    assert body["analysed"] is True
    assert body["healthy"] is False
    assert body["root_cause_stage"] == "retrieval"
    assert body["diagnostic_confidence"] > 0.5


def test_ingesting_a_healthy_trace_reports_it_healthy(ingest):
    body = ingest(healthy_trace())

    assert body["healthy"] is True
    assert body["root_cause_stage"] is None


def test_events_are_counted_and_stored(client, ingest):
    factory = TraceFactory()
    span = factory.add("retriever", Stage.RETRIEVAL, outputs={"documents": []})
    span.record_event("cache_miss", key="q")
    span.record_event("fallback", used=True)
    trace = factory.finish()

    assert ingest(trace)["events_ingested"] == 2
    stored = client.get(f"/api/v1/traces/{trace.trace_id}/spans").json()
    assert [e["name"] for e in stored[0]["events"]] == ["cache_miss", "fallback"]


def test_re_ingesting_the_same_trace_replaces_it(client, ingest):
    # A retried export must not fail. The trace is immutable in practice, so
    # the second copy is the same data.
    trace = retrieval_failure_trace()
    ingest(trace)
    ingest(trace)

    assert client.get("/api/v1/traces").json()["total"] == 1


def test_a_trace_round_trips_through_the_api(client, ingest):
    original = retrieval_failure_trace()
    ingest(original)
    from tracelens.models import Trace

    restored = Trace.model_validate(client.get(f"/api/v1/traces/{original.trace_id}").json())

    assert restored.name == original.name
    assert [s.name for s in restored.spans] == [s.name for s in original.spans]
    assert restored.spans[1].outputs == original.spans[1].outputs
    assert restored.spans[1].stage is Stage.RETRIEVAL


# -- malformed requests ---------------------------------------------------


def test_a_malformed_trace_is_rejected_with_422(client):
    response = client.post("/api/v1/traces", json={"name": "missing everything else", "spans": 3})
    assert response.status_code == 422


def test_a_trace_with_a_bad_id_is_rejected(client):
    response = client.post("/api/v1/traces", json={"trace_id": "not-hex", "name": "x"})

    assert response.status_code == 422
    assert "malformed trace_id" in response.text


def test_a_span_ending_before_it_starts_is_rejected(client):
    trace = healthy_trace()
    payload = trace.model_dump(mode="json")
    payload["spans"][0]["end_time"] = payload["spans"][0]["start_time"]
    payload["spans"][0]["start_time"] = (T0 + timedelta(seconds=99)).isoformat()

    response = client.post("/api/v1/traces", json=payload)
    assert response.status_code == 422
    assert "ends before it starts" in response.text


def test_an_empty_body_is_rejected(client):
    assert client.post("/api/v1/traces", json={}).status_code == 422


def test_an_unknown_field_is_rejected(client):
    payload = healthy_trace().model_dump(mode="json")
    payload["surprise"] = "value"

    assert client.post("/api/v1/traces", json=payload).status_code == 422


# -- missing resources ----------------------------------------------------


MISSING = "a" * 32


def test_an_unknown_trace_is_404(client):
    response = client.get(f"/api/v1/traces/{MISSING}")

    assert response.status_code == 404
    assert MISSING in response.json()["detail"]


@pytest.mark.parametrize("suffix", ["/spans", "/failures", "/root-cause"])
def test_sub_resources_of_an_unknown_trace_are_404(client, suffix):
    assert client.get(f"/api/v1/traces/{MISSING}{suffix}").status_code == 404


def test_analysing_an_unknown_trace_is_404(client):
    assert client.post(f"/api/v1/traces/{MISSING}/analyse").status_code == 404


def test_deleting_an_unknown_trace_is_404(client):
    assert client.delete(f"/api/v1/traces/{MISSING}").status_code == 404


# -- listing, filtering, pagination ---------------------------------------


@pytest.fixture
def several_traces(ingest):
    traces = []
    for index in range(7):
        # Spread start times a minute apart so ordering is unambiguous.
        factory = TraceFactory(
            f"run-{index}",
            project="alpha" if index < 4 else "beta",
            started_at=T0 + timedelta(minutes=index),
        )
        factory.trace.pipeline = "rag" if index % 2 == 0 else "chat"
        factory.add(
            "retriever",
            Stage.RETRIEVAL,
            inputs={"query": QUESTION},
            outputs={"documents": [{"id": "d", "text": "refund policy 30 days"}]},
        )
        trace = factory.finish()
        ingest(trace)
        traces.append(trace)
    return traces


def test_listing_returns_newest_first(client, several_traces):
    items = client.get("/api/v1/traces").json()["items"]

    assert [i["name"] for i in items] == [f"run-{i}" for i in range(6, -1, -1)]


def test_pagination_reports_totals_and_more(client, several_traces):
    first = client.get("/api/v1/traces?limit=3").json()

    assert first["total"] == 7
    assert len(first["items"]) == 3
    assert first["has_more"] is True

    last = client.get("/api/v1/traces?limit=3&offset=6").json()
    assert len(last["items"]) == 1
    assert last["has_more"] is False


def test_pages_do_not_overlap(client, several_traces):
    page_one = client.get("/api/v1/traces?limit=3&offset=0").json()["items"]
    page_two = client.get("/api/v1/traces?limit=3&offset=3").json()["items"]

    assert not {i["trace_id"] for i in page_one} & {i["trace_id"] for i in page_two}


def test_an_offset_past_the_end_returns_an_empty_page(client, several_traces):
    body = client.get("/api/v1/traces?offset=500").json()

    assert body["items"] == []
    assert body["total"] == 7
    assert body["has_more"] is False


@pytest.mark.parametrize(
    ("query", "expected"), [("limit=0", 422), ("limit=999", 422), ("offset=-1", 422)]
)
def test_pagination_bounds_are_enforced(client, query, expected):
    # Without this a single request could read the whole table.
    assert client.get(f"/api/v1/traces?{query}").status_code == expected


def test_filtering_by_project(client, several_traces):
    assert client.get("/api/v1/traces?project=alpha").json()["total"] == 4
    assert client.get("/api/v1/traces?project=beta").json()["total"] == 3
    assert client.get("/api/v1/traces?project=nothing").json()["total"] == 0


def test_filtering_by_pipeline(client, several_traces):
    assert client.get("/api/v1/traces?pipeline=rag").json()["total"] == 4


def test_filtering_by_stage(client, several_traces, ingest):
    ingest(healthy_trace())

    assert client.get("/api/v1/traces?stage=validation").json()["total"] == 1
    assert client.get("/api/v1/traces?stage=retrieval").json()["total"] == 8


def test_filtering_by_name_search(client, several_traces):
    assert client.get("/api/v1/traces?search=run-3").json()["total"] == 1


def test_filtering_by_time_window(client, several_traces):
    # Passed as params, not interpolated: an unencoded "+00:00" offset decodes
    # as a space and is not a timestamp any more.
    since = (T0 + timedelta(minutes=5)).isoformat()
    body = client.get("/api/v1/traces", params={"since": since}).json()

    assert body["total"] == 2


def test_a_javascript_style_z_timestamp_is_accepted(client, several_traces):
    # Date.toISOString() is what a browser client will send.
    since = (T0 + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")

    assert client.get("/api/v1/traces", params={"since": since}).json()["total"] == 2


def test_a_summary_carries_the_diagnosis(client, ingest):
    trace = retrieval_failure_trace()
    ingest(trace)
    item = client.get("/api/v1/traces").json()["items"][0]

    assert item["root_cause_stage"] == "retrieval"
    assert item["analysed"] is True
    assert item["span_count"] == 4


# -- spans, failures, reports ---------------------------------------------


def test_spans_are_returned_in_execution_order(client, ingest):
    trace = retrieval_failure_trace()
    ingest(trace)
    spans = client.get(f"/api/v1/traces/{trace.trace_id}/spans").json()

    assert [s["name"] for s in spans] == [s.name for s in trace.spans]
    assert spans[1]["stage"] == "retrieval"


def test_failures_are_persisted_at_ingest(client, ingest):
    trace = retrieval_failure_trace()
    ingest(trace)
    failures = client.get(f"/api/v1/traces/{trace.trace_id}/failures").json()

    assert len(failures) == 2
    assert {f["category"] for f in failures} == {"retrieval_failure"}
    assert all(f["evidence"] for f in failures)


def test_a_healthy_trace_records_no_failures(client, ingest):
    trace = healthy_trace()
    ingest(trace)

    assert client.get(f"/api/v1/traces/{trace.trace_id}/failures").json() == []


def test_the_root_cause_report_is_served_from_storage(client, ingest):
    trace = retrieval_failure_trace()
    ingest(trace)
    report = client.get(f"/api/v1/traces/{trace.trace_id}/root-cause").json()

    assert report["first_divergence_stage"] == "retrieval"
    assert report["likely_root_cause"]["stage"] == "retrieval"
    assert report["evidence_chain"]
    assert report["recommended_actions"]


def test_re_analysis_replaces_the_stored_report(client, ingest):
    trace = retrieval_failure_trace()
    ingest(trace)
    first = client.get(f"/api/v1/traces/{trace.trace_id}/root-cause").json()
    again = client.post(f"/api/v1/traces/{trace.trace_id}/analyse").json()

    assert again["first_divergence_stage"] == first["first_divergence_stage"]
    assert again["generated_at"] >= first["generated_at"]


def test_deleting_a_trace_removes_its_spans_and_report(client, ingest):
    trace = retrieval_failure_trace()
    ingest(trace)

    assert client.delete(f"/api/v1/traces/{trace.trace_id}").status_code == 204
    assert client.get(f"/api/v1/traces/{trace.trace_id}").status_code == 404
    assert client.get("/api/v1/traces").json()["total"] == 0


# -- streaming ingestion --------------------------------------------------


def test_a_span_can_be_appended_to_an_existing_trace(client, ingest):
    from tracelens.models import Span

    trace = healthy_trace()
    ingest(trace)
    extra = Span(trace_id=trace.trace_id, name="late-step", stage=Stage.POSTPROCESSING)

    response = client.post(
        "/api/v1/spans",
        content=extra.model_dump_json(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 202
    assert len(client.get(f"/api/v1/traces/{trace.trace_id}/spans").json()) == 6


def test_a_span_for_an_unknown_trace_is_404(client):
    from tracelens.models import Span

    orphan = Span(trace_id=MISSING, name="orphan")
    response = client.post(
        "/api/v1/spans",
        content=orphan.model_dump_json(),
        headers={"content-type": "application/json"},
    )

    # Inventing a trace would leave it with no name, project, or start time and
    # corrupt every aggregate that reads them.
    assert response.status_code == 404


def test_an_event_can_be_appended_to_an_existing_span(client, ingest):
    trace = healthy_trace()
    ingest(trace)
    span_id = trace.spans[0].span_id

    response = client.post(
        "/api/v1/events",
        json={
            "trace_id": trace.trace_id,
            "span_id": span_id,
            "event": {"name": "late-event", "attributes": {"n": 1}},
        },
    )
    assert response.status_code == 202

    spans = client.get(f"/api/v1/traces/{trace.trace_id}/spans").json()
    assert spans[0]["events"][0]["name"] == "late-event"


def test_an_event_for_a_span_in_another_trace_is_404(client, ingest):
    trace = healthy_trace()
    ingest(trace)

    response = client.post(
        "/api/v1/events",
        json={"trace_id": MISSING, "span_id": trace.spans[0].span_id, "event": {"name": "stray"}},
    )
    assert response.status_code == 404


# -- aggregates -----------------------------------------------------------


def test_the_overview_separates_execution_failures_from_diagnosed_ones(client, ingest):
    # The retrieval trace raises nothing at all: every span reports ok. An APM
    # would call this pipeline healthy while the user received a wrong answer.
    ingest(retrieval_failure_trace())
    ingest(healthy_trace())
    body = client.get("/api/v1/overview").json()

    assert body["total_traces"] == 2
    assert body["failed_traces"] == 0
    assert body["failure_rate"] == 0.0
    assert body["diagnosed_failure_rate"] == 0.5
    assert body["root_causes_identified"] == 1
    assert body["top_failure_stages"] == [{"stage": "retrieval", "count": 1}]


def test_the_overview_of_an_empty_database_is_all_zeroes(client):
    body = client.get("/api/v1/overview").json()

    assert body["total_traces"] == 0
    assert body["failure_rate"] == 0.0
    assert body["projects"] == []


def test_pipeline_health_groups_by_project_and_pipeline(client, several_traces):
    rows = client.get("/api/v1/pipelines/health").json()

    assert {(r["project"], r["pipeline"]) for r in rows} == {
        ("alpha", "rag"),
        ("alpha", "chat"),
        ("beta", "rag"),
        ("beta", "chat"),
    }
    assert sum(r["total_traces"] for r in rows) == 7


def test_pipeline_health_can_be_scoped_to_a_project(client, several_traces):
    rows = client.get("/api/v1/pipelines/health?project=alpha").json()

    assert {r["project"] for r in rows} == {"alpha"}


def test_the_failure_breakdown_counts_by_category_and_stage(client, ingest):
    ingest(retrieval_failure_trace())
    rows = client.get("/api/v1/failures/breakdown").json()

    assert rows == [{"category": "retrieval_failure", "stage": "retrieval", "count": 2}]
