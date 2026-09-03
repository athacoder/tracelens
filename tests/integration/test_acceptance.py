"""The final acceptance test (section 36).

Walks the entire product in one test, through the same public surfaces a user
would: the SDK instruments a pipeline, the HTTP API ingests the trace, the
forensic engine diagnoses it, the API serves the diagnosis, and the benchmark
evaluator scores that diagnosis against ground truth.

Nothing here reaches past a public interface. The pipeline is instrumented with
the published SDK, ingestion goes over HTTP, and every assertion reads a
response body rather than an internal object — so this test failing means a
user-visible promise broke, not that an implementation detail moved.
"""

from __future__ import annotations

import pytest
from app.storage import database
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from tracelens import MemoryExporter, Tracer
from tracelens.models import CaptureMode, Stage

from benchmark.corpus import QUESTIONS
from benchmark.metrics import CaseResult, score
from benchmark.pipeline import run_pipeline
from benchmark.scenarios import get_scenario

QUESTION = QUESTIONS[0]


@pytest.fixture
def client():
    """A running TraceLens: schema created, API served, storage isolated."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    database.configure(engine)
    database.create_all(engine)

    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client

    database.drop_all(engine)
    engine.dispose()


def run_and_ingest(client: TestClient, scenario: str) -> dict:
    """Run the pipeline through the SDK and ship the trace over HTTP."""
    tracer = Tracer(
        project="acceptance",
        pipeline="rag",
        exporter=MemoryExporter(),
        capture=CaptureMode.FULL,
    )
    outcome = run_pipeline(QUESTION, scenario, tracer=tracer)

    response = client.post(
        "/api/v1/traces",
        content=outcome.trace.model_dump_json(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 202, response.text
    return {"trace": outcome.trace, "answer": outcome.answer, "ingest": response.json()}


def test_the_full_acceptance_flow(client: TestClient):
    """The fifteen steps of section 36, in order."""

    # 1. TraceLens is running and can reach its storage.
    health = client.get("/api/v1/health").json()
    assert health["status"] == "ok"
    assert health["database_ok"] is True

    # 2-3. A known-good RAG run is recorded and confirmed healthy.
    good = run_and_ingest(client, "healthy")
    assert good["answer"] == QUESTION.expected_answer
    assert good["ingest"]["healthy"] is True
    assert good["ingest"]["root_cause_stage"] is None

    good_report = client.get(f"/api/v1/traces/{good['trace'].trace_id}/root-cause").json()
    assert good_report["healthy"] is True
    assert good_report["evidence_chain"] == []
    assert client.get(f"/api/v1/traces/{good['trace'].trace_id}/failures").json() == []

    # 4-5. A known retriever failure is injected and the pipeline is re-run.
    scenario = get_scenario("outdated_document")
    bad = run_and_ingest(client, scenario.name)
    assert bad["answer"] != QUESTION.expected_answer

    # The run raised nothing: this is the failure an error-rate dashboard
    # cannot see, which is the whole point of the exercise.
    assert bad["trace"].status.value == "ok"
    assert not any(span.failed for span in bad["trace"].spans)

    # 6. TraceLens detects the failure.
    assert bad["ingest"]["healthy"] is False
    failures = client.get(f"/api/v1/traces/{bad['trace'].trace_id}/failures").json()
    assert failures, "the injected failure produced no findings"

    # 7. It identifies the retriever as the first divergence.
    report = client.get(f"/api/v1/traces/{bad['trace'].trace_id}/root-cause").json()
    assert report["first_divergence_stage"] == Stage.RETRIEVAL.value
    assert report["likely_root_cause"]["stage"] == Stage.RETRIEVAL.value
    assert report["likely_root_cause"]["confidence"] > 0.5

    # 8. It shows evidence, each item anchored to a real span.
    evidence = report["evidence_chain"]
    assert len(evidence) >= 2
    span_ids = {span.span_id for span in bad["trace"].spans}
    for item in evidence:
        assert item["span_id"] in span_ids

    # The argument, not just the claim: the stages after the retriever are
    # explicitly cleared rather than merely unmentioned.
    exculpatory = [e for e in evidence if e["detail"].get("role") == "exculpatory"]
    assert {e["stage"] for e in exculpatory} >= {Stage.PROMPT_BUILD.value, Stage.LLM.value}

    # 9. It identifies downstream impact and proposes remediation.
    assert report["recommended_actions"]
    assert "retriever" in report["recommended_actions"][0].lower()
    assert report["divergence"]["assessments"]

    # 10-11. The trace is recorded and served the way the dashboard reads it.
    listing = client.get("/api/v1/traces").json()
    assert listing["total"] == 2
    summaries = {item["trace_id"]: item for item in listing["items"]}
    assert summaries[bad["trace"].trace_id]["root_cause_stage"] == Stage.RETRIEVAL.value
    assert summaries[good["trace"].trace_id]["root_cause_stage"] is None

    spans = client.get(f"/api/v1/traces/{bad['trace'].trace_id}/spans").json()
    assert [s["stage"] for s in spans] == [s.stage.value for s in bad["trace"].spans]

    overview = client.get("/api/v1/overview").json()
    assert overview["total_traces"] == 2
    assert overview["root_causes_identified"] == 1
    # No span raised, so the execution failure rate is zero while the
    # diagnosed rate is not. That gap is the product.
    assert overview["failure_rate"] == 0.0
    assert overview["diagnosed_failure_rate"] == 0.5
    assert overview["top_failure_stages"] == [{"stage": Stage.RETRIEVAL.value, "count": 1}]

    # 12. The benchmark evaluator scores the diagnosis against ground truth.
    scored = score(
        [
            CaseResult(
                scenario=scenario.name,
                question_id=QUESTION.id,
                trace_id=bad["trace"].trace_id,
                failure_present=scenario.failure_present,
                expected_stage=scenario.root_stage,
                detected_failure=not report["healthy"],
                predicted_stage=Stage(report["first_divergence_stage"]),
                predicted_confidence=report["likely_root_cause"]["confidence"],
                analysis_ms=report["analysis_ms"],
            ),
            CaseResult(
                scenario="healthy",
                question_id=QUESTION.id,
                trace_id=good["trace"].trace_id,
                failure_present=False,
                expected_stage=None,
                detected_failure=not good_report["healthy"],
                predicted_stage=None,
                predicted_confidence=0.0,
                analysis_ms=good_report["analysis_ms"],
            ),
        ]
    )
    assert scored.root_cause_accuracy == 1.0
    assert scored.false_positive_rate == 0.0
    assert scored.recall == 1.0


def test_the_semantic_layer_explains_the_same_finding(client: TestClient):
    """The optional layer runs on the default path with no API key."""
    bad = run_and_ingest(client, "outdated_document")

    semantic = client.post(f"/api/v1/traces/{bad['trace'].trace_id}/semantic").json()

    assert semantic["provider"] == "mock"
    assert semantic["analysis"]["likely_root_cause"] == Stage.RETRIEVAL.value
    assert semantic["disagrees_with_deterministic"] is False
    assert semantic["prompt_version"]


def test_a_second_diagnosis_is_reproducible(client: TestClient):
    """Re-analysing a stored trace reaches the same conclusion."""
    bad = run_and_ingest(client, "outdated_document")
    trace_id = bad["trace"].trace_id

    first = client.get(f"/api/v1/traces/{trace_id}/root-cause").json()
    second = client.post(f"/api/v1/traces/{trace_id}/analyse").json()

    assert second["first_divergence_stage"] == first["first_divergence_stage"]
    assert second["likely_root_cause"]["score"] == first["likely_root_cause"]["score"]
    assert len(second["evidence_chain"]) == len(first["evidence_chain"])


@pytest.mark.parametrize(
    ("scenario_name", "expected_stage"),
    [
        ("outdated_document", Stage.RETRIEVAL),
        ("unsupported_claim", Stage.LLM),
        ("postprocessing_corruption", Stage.POSTPROCESSING),
    ],
)
def test_the_three_demo_scenarios_receive_three_diagnoses(
    client: TestClient, scenario_name: str, expected_stage: Stage
):
    """Section 33, verified through the API rather than in memory.

    The same wrong-looking answer gets a different diagnosis depending on
    which stage actually caused it. That distinction is the product.
    """
    outcome = run_and_ingest(client, scenario_name)
    report = client.get(f"/api/v1/traces/{outcome['trace'].trace_id}/root-cause").json()

    assert report["first_divergence_stage"] == expected_stage.value
    assert report["summary"]
    assert report["recommended_actions"]


def test_the_pipeline_is_traced_through_the_public_sdk_only(client: TestClient):
    """The engine sees what a user's pipeline would give it, and no more."""
    outcome = run_and_ingest(client, "outdated_document")
    trace = outcome["trace"]

    # Every stage recorded, structurally sound, no privileged annotation.
    assert len(trace.spans) == 9
    assert trace.structural_errors() == []
    for span in trace.spans:
        assert "root_cause" not in span.attributes
        assert "ground_truth" not in span.attributes
