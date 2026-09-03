"""Phase 5 acceptance: the failure detection engine."""

from __future__ import annotations

import pytest
from app.detection import (
    DetectionConfig,
    FailureCategory,
    StageSchema,
    detect_execution_failure,
    detect_latency_anomaly,
    detect_missing_information,
    detect_retrieval_failure,
    detect_semantic_inconsistency,
    detect_structural_anomaly,
    detect_unsupported_claims,
    run_detectors,
    validate_schema,
)
from app.detection.models import EvidenceKind
from factories import CURRENT_POLICY, QUESTION, UNRELATED_DOC, TraceFactory
from tracelens.models import ErrorInfo, Severity, SpanStatus, Stage

# -- the control case ----------------------------------------------------


def test_a_healthy_trace_produces_no_findings(healthy):
    # The most important test in the file. A forensic tool that cries wolf on
    # working pipelines will not be used on broken ones.
    assert run_detectors(healthy) == []


# -- execution failures --------------------------------------------------


def test_execution_failure_is_detected_with_full_confidence(tool_timeout):
    found = detect_execution_failure(tool_timeout)

    assert len(found) == 1
    candidate = found[0]
    assert candidate.category is FailureCategory.EXECUTION_ERROR
    assert candidate.stage is Stage.TOOL
    assert candidate.confidence == 1.0
    assert candidate.severity is Severity.CRITICAL
    assert candidate.evidence[0].kind is EvidenceKind.OBSERVED
    assert "TimeoutError" in candidate.summary
    assert candidate.evidence[0].detail["error_type"] == "TimeoutError"


def test_a_span_marked_failed_without_an_error_is_still_reported():
    factory = TraceFactory()
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        outputs={"documents": [CURRENT_POLICY]},
        status=SpanStatus.ERROR,
    )
    found = detect_execution_failure(factory.finish())

    assert len(found) == 1
    assert "without an attached error" in found[0].summary


def test_execution_failures_are_reported_in_execution_order():
    factory = TraceFactory()
    factory.add("first", Stage.RETRIEVAL, error=ErrorInfo(type="LookupError"))
    factory.add("second", Stage.LLM, error=ErrorInfo(type="TimeoutError"))
    found = detect_execution_failure(factory.finish())

    assert [c.summary.split()[0] for c in found] == ["first", "second"]


def test_healthy_spans_produce_no_execution_findings(healthy):
    assert detect_execution_failure(healthy) == []


# -- schema validation ---------------------------------------------------


def test_missing_required_output_field_is_a_schema_violation():
    factory = TraceFactory()
    factory.add("retriever", Stage.RETRIEVAL, outputs={"results_count": 3})
    found = validate_schema(factory.finish())

    assert len(found) == 1
    assert found[0].category is FailureCategory.SCHEMA_VIOLATION
    assert found[0].evidence[0].detail["missing_fields"] == ["documents"]


def test_wrong_output_type_is_a_schema_violation():
    factory = TraceFactory()
    factory.add("retriever", Stage.RETRIEVAL, outputs={"documents": "one document"})
    found = validate_schema(factory.finish())

    assert "documents is str, expected list or tuple" in found[0].evidence[0].detail["type_errors"]


def test_any_of_is_satisfied_by_any_one_alternative():
    factory = TraceFactory()
    factory.add("llm", Stage.LLM, outputs={"completion": "an answer"})
    assert validate_schema(factory.finish()) == []


def test_a_stage_producing_none_of_its_alternatives_is_flagged():
    factory = TraceFactory()
    factory.add("llm", Stage.LLM, outputs={"tokens": 40})
    found = validate_schema(factory.finish())

    assert found[0].evidence[0].detail["expected_any_of"]


def test_a_span_can_declare_its_own_contract():
    factory = TraceFactory()
    factory.add(
        "custom",
        Stage.OTHER,
        outputs={"a": 1},
        attributes={"expects_outputs": ["a", "b"]},
    )
    found = validate_schema(factory.finish())

    assert found[0].evidence[0].detail["missing_fields"] == ["b"]


def test_a_failed_span_is_not_also_blamed_for_producing_no_output(tool_timeout):
    # The tool span timed out and emitted nothing. That is one failure, owned
    # by the execution detector, not two.
    assert validate_schema(tool_timeout) == []


def test_custom_schemas_replace_the_defaults():
    factory = TraceFactory()
    factory.add("retriever", Stage.RETRIEVAL, outputs={"passages": []})
    schemas = {Stage.RETRIEVAL: StageSchema(required=("passages",))}

    assert validate_schema(factory.finish(), schemas) == []


# -- missing information -------------------------------------------------


def test_empty_output_collection_is_missing_information():
    factory = TraceFactory()
    factory.add("retriever", Stage.RETRIEVAL, inputs={"query": QUESTION}, outputs={"documents": []})
    found = detect_missing_information(factory.finish())

    assert found[0].category is FailureCategory.MISSING_INFORMATION
    assert found[0].severity is Severity.HIGH
    assert found[0].evidence[0].detail["empty_fields"] == ["documents"]


def test_declared_required_field_that_never_arrived_is_reported():
    factory = TraceFactory()
    factory.add(
        "preprocess",
        Stage.PREPROCESSING,
        outputs={"query": QUESTION},
        attributes={"required_fields": ["query", "user_id"]},
    )
    found = detect_missing_information(factory.finish())

    assert found[0].evidence[0].detail["absent"] == ["user_id"]


def test_a_prompt_that_dropped_every_retrieved_document_is_reported():
    factory = TraceFactory()
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION},
        outputs={"documents": [CURRENT_POLICY]},
    )
    factory.add(
        "prompt-builder",
        Stage.PROMPT_BUILD,
        inputs={"query": QUESTION, "documents": [CURRENT_POLICY]},
        outputs={"prompt": f"Question: {QUESTION}"},  # context silently dropped
    )
    found = detect_missing_information(factory.finish())

    dropped = [c for c in found if "none of the" in c.summary]
    assert len(dropped) == 1
    assert dropped[0].stage is Stage.PROMPT_BUILD
    assert dropped[0].evidence[0].kind is EvidenceKind.COMPARISON


def test_a_prompt_that_carried_the_context_is_not_reported(healthy):
    assert detect_missing_information(healthy) == []


# -- latency -------------------------------------------------------------


def test_latency_without_a_baseline_is_low_confidence(tool_timeout):
    found = detect_latency_anomaly(tool_timeout)

    assert len(found) == 1
    assert found[0].confidence < 0.5
    assert found[0].evidence[0].kind is EvidenceKind.HEURISTIC
    assert "no baseline" in found[0].summary


def test_latency_against_a_baseline_is_a_real_comparison(tool_timeout):
    found = detect_latency_anomaly(tool_timeout, baselines={Stage.TOOL: 100.0})

    tool = next(c for c in found if c.stage is Stage.TOOL)
    assert tool.confidence > 0.5
    assert tool.evidence[0].kind is EvidenceKind.COMPARISON
    assert tool.evidence[0].detail["baseline_ms"] == 100.0


def test_a_fast_span_is_never_a_latency_anomaly(healthy):
    # Every span here is well under the absolute floor.
    assert detect_latency_anomaly(healthy) == []


def test_a_dominant_but_tiny_span_is_not_flagged():
    # 100% of a 20ms trace is still 20ms. Share alone must not fire.
    factory = TraceFactory()
    factory.add("only-step", Stage.LLM, duration_s=0.02, outputs={"answer": "hi"})
    assert detect_latency_anomaly(factory.finish()) == []


def test_a_span_within_its_baseline_is_not_flagged(healthy):
    assert detect_latency_anomaly(healthy, baselines={Stage.LLM: 1000.0}) == []


# -- semantic inconsistency ----------------------------------------------


def test_an_answer_unsupported_by_its_own_prompt_is_flagged(model_failure):
    found = detect_semantic_inconsistency(model_failure)

    assert len(found) == 1
    assert found[0].stage is Stage.LLM
    assert found[0].confidence == pytest.approx(0.8)
    assert "14" in found[0].evidence[0].detail["ungrounded_claims"]


def test_an_answer_faithful_to_a_wrong_document_is_not_a_model_failure(retrieval_failure):
    # The heart of the scenario A / scenario B distinction. The model answered
    # 90 days because its prompt said 90 days. The model did nothing wrong.
    assert detect_semantic_inconsistency(retrieval_failure) == []


def test_a_postprocessor_that_changes_a_number_is_flagged(postprocessing_failure):
    found = detect_semantic_inconsistency(postprocessing_failure)

    assert len(found) == 1
    assert found[0].stage is Stage.POSTPROCESSING
    assert "30 became 3" in found[0].summary
    assert found[0].evidence[0].detail["numbers_dropped"] == ["30"]
    assert found[0].evidence[0].detail["numbers_introduced"] == ["3"]


def test_a_postprocessor_that_only_reformats_is_not_flagged():
    factory = TraceFactory()
    factory.add(
        "formatter",
        Stage.POSTPROCESSING,
        inputs={"answer": "Customers have 30 days to return an item."},
        outputs={"answer": "**Customers have 30 days to return an item.**"},
    )
    assert detect_semantic_inconsistency(factory.finish()) == []


def test_a_postprocessor_that_only_drops_detail_is_not_flagged():
    # Summarising is a legitimate transformation: values removed but none
    # invented. Only replacement counts as corruption.
    factory = TraceFactory()
    factory.add(
        "summariser",
        Stage.POSTPROCESSING,
        inputs={"answer": "Returns take 30 days and refunds take 5 days."},
        outputs={"answer": "Returns take 30 days."},
    )
    assert detect_semantic_inconsistency(factory.finish()) == []


def test_healthy_llm_output_is_not_flagged(healthy):
    assert detect_semantic_inconsistency(healthy) == []


# -- retrieval -----------------------------------------------------------


def test_empty_retrieval_is_critical():
    factory = TraceFactory()
    factory.add("retriever", Stage.RETRIEVAL, inputs={"query": QUESTION}, outputs={"documents": []})
    found = detect_retrieval_failure(factory.finish())

    assert found[0].severity is Severity.CRITICAL
    assert "returned no documents" in found[0].summary


def test_a_stale_document_is_detected_from_its_own_metadata(retrieval_failure):
    found = detect_retrieval_failure(retrieval_failure)
    stale = [c for c in found if "stale" in c.summary]

    assert len(stale) == 1
    assert stale[0].evidence[0].detail["stale_documents"][0]["id"] == "policy-2019"
    assert "superseded by policy-2026" in stale[0].summary


def test_an_expired_document_is_detected_by_date():
    factory = TraceFactory()
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION},
        outputs={
            "documents": [
                {"id": "old", "text": "Returns within 90 days.", "valid_until": "2025-12-31"}
            ]
        },
    )
    found = detect_retrieval_failure(factory.finish())

    assert any("expired on 2025-12-31" in c.summary for c in found)


def test_a_missing_expected_document_is_near_certain(retrieval_failure):
    found = detect_retrieval_failure(retrieval_failure)
    missing = [c for c in found if "expected document" in c.summary]

    assert missing[0].confidence >= 0.9
    assert missing[0].evidence[0].kind is EvidenceKind.RULE
    assert missing[0].evidence[0].detail["expected"] == ["policy-2026"]


def test_an_off_topic_document_is_flagged_as_irrelevant():
    factory = TraceFactory()
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION},
        outputs={"documents": [UNRELATED_DOC]},
    )
    found = detect_retrieval_failure(factory.finish())

    assert any(c.category is FailureCategory.RETRIEVAL_FAILURE for c in found)
    assert any("do not address" in c.summary for c in found)


def test_relevant_current_retrieval_is_clean(healthy):
    assert detect_retrieval_failure(healthy) == []


# -- unsupported claims --------------------------------------------------


def test_a_claim_absent_from_every_source_is_flagged(model_failure):
    found = detect_unsupported_claims(model_failure)

    assert len(found) == 1
    assert found[0].category is FailureCategory.UNSUPPORTED_CLAIM
    assert "14" in found[0].evidence[0].detail["unsupported"]


def test_an_answer_grounded_in_a_stale_document_is_not_an_unsupported_claim(retrieval_failure):
    # The answer is wrong, but it is not unsupported: the retrieved document
    # says exactly this. Blaming the model here would be the wrong diagnosis.
    assert detect_unsupported_claims(retrieval_failure) == []


def test_the_final_answer_is_taken_from_the_last_stage(postprocessing_failure):
    found = detect_unsupported_claims(postprocessing_failure)

    # "3" was introduced by the post-processor, after the model ran.
    assert "3" in found[0].evidence[0].detail["unsupported"]
    assert found[0].stage is Stage.POSTPROCESSING


def test_a_grounded_answer_is_not_flagged(healthy):
    assert detect_unsupported_claims(healthy) == []


# -- structural ----------------------------------------------------------


def test_a_span_referencing_a_missing_parent_is_a_structural_anomaly():
    factory = TraceFactory()
    span = factory.add("orphan", Stage.RETRIEVAL, outputs={"documents": [CURRENT_POLICY]})
    span.parent_span_id = "f" * 16
    found = detect_structural_anomaly(factory.finish())

    assert found[0].category is FailureCategory.STRUCTURAL_ANOMALY
    assert "missing parent" in found[0].evidence[0].detail["problems"][0]


def test_a_well_formed_trace_has_no_structural_findings(healthy):
    assert detect_structural_anomaly(healthy) == []


# -- the combined pass ---------------------------------------------------


def test_run_detectors_orders_findings_by_execution_position(tool_timeout):
    found = run_detectors(tool_timeout)
    stages = [c.stage for c in found]

    assert stages.index(Stage.TOOL) < stages.index(Stage.LLM)


def test_run_detectors_separates_the_three_scenarios(
    retrieval_failure, model_failure, postprocessing_failure
):
    # Each scenario must produce findings on a different stage. This is the
    # property the whole system exists to provide.
    assert {c.stage for c in run_detectors(retrieval_failure)} == {Stage.RETRIEVAL}
    assert {c.stage for c in run_detectors(model_failure)} == {Stage.LLM}
    assert {c.stage for c in run_detectors(postprocessing_failure)} == {Stage.POSTPROCESSING}


def test_config_thresholds_are_honoured():
    factory = TraceFactory()
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION},
        outputs={"documents": [UNRELATED_DOC]},
    )
    trace = factory.finish()

    strict = run_detectors(trace, DetectionConfig(relevance_threshold=0.9))
    permissive = run_detectors(trace, DetectionConfig(relevance_threshold=0.0))

    assert any("do not address" in c.summary for c in strict)
    assert not any("do not address" in c.summary for c in permissive)


def test_candidate_weight_ranks_observed_facts_above_heuristics(tool_timeout):
    found = run_detectors(tool_timeout)
    execution = next(c for c in found if c.category is FailureCategory.EXECUTION_ERROR)
    latency = next(c for c in found if c.category is FailureCategory.LATENCY_ANOMALY)

    assert execution.weight > latency.weight
    assert execution.evidence_strength == 1.0


def test_evidence_carries_the_span_it_came_from(retrieval_failure):
    retriever = retrieval_failure.stage_spans(Stage.RETRIEVAL)[0]
    for candidate in run_detectors(retrieval_failure):
        assert candidate.span_id == retriever.span_id
        for item in candidate.evidence:
            assert item.span_id == retriever.span_id
