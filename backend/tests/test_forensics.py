"""Phase 7 and 8 acceptance: first divergence, ranking, and the report."""

from __future__ import annotations

import pytest
from app.detection.models import FailureCategory
from app.forensics import (
    Verdict,
    build_evidence_chain,
    collect_candidates,
    direct_dependencies,
    downstream_of,
    find_first_divergence,
    generate_root_cause_report,
    rank_failure_candidates,
    shares_data,
    validate_stage_transition,
)
from factories import CURRENT_POLICY, OUTDATED_POLICY, QUESTION, TraceFactory
from tracelens.models import ErrorInfo, Stage

# -- dependency inference -------------------------------------------------


def test_data_flow_links_a_producer_to_its_consumer():
    factory = TraceFactory()
    retriever = factory.add("retriever", Stage.RETRIEVAL, outputs={"documents": [CURRENT_POLICY]})
    builder = factory.add(
        "prompt-builder",
        Stage.PROMPT_BUILD,
        inputs={"documents": [CURRENT_POLICY]},
        outputs={"prompt": "..."},
    )

    assert shares_data(retriever, builder)
    assert retriever.span_id in direct_dependencies(factory.finish())[builder.span_id]


def test_unrelated_payloads_are_not_linked_by_data_flow():
    factory = TraceFactory()
    a = factory.add("weather", Stage.TOOL, outputs={"forecast": "heavy rain in Oslo tomorrow"})
    b = factory.add("billing", Stage.TOOL, inputs={"invoice": "quarterly subscription charge"})

    assert not shares_data(a, b)


def test_nesting_creates_a_dependency():
    factory = TraceFactory()
    parent = factory.add("pipeline", Stage.OTHER, duration_s=1.0)
    child = factory.add("retriever", Stage.RETRIEVAL, outputs={"documents": []})
    child.parent_span_id = parent.span_id

    assert parent.span_id in direct_dependencies(factory.finish())[child.span_id]


def test_the_linear_fallback_links_uninstrumented_stages():
    # Neither span recorded a payload, so only sequence is available.
    factory = TraceFactory()
    first = factory.add("step-a", Stage.PREPROCESSING)
    second = factory.add("step-b", Stage.RETRIEVAL)

    assert direct_dependencies(factory.finish())[second.span_id] == {first.span_id}


def test_a_span_that_produced_nothing_still_feeds_the_next_one():
    # Regression: a timed-out tool has no output to match on, so data flow
    # cannot link it, and the stage it broke was being reported as unrelated.
    factory = TraceFactory()
    factory.add("preprocess", Stage.PREPROCESSING, outputs={"query": QUESTION})
    tool = factory.add("order-api", Stage.TOOL, error=ErrorInfo(type="TimeoutError"))
    llm = factory.add("llm", Stage.LLM, inputs={"prompt": "..."}, outputs={"answer": "..."})
    trace = factory.finish()

    assert tool.span_id in direct_dependencies(trace)[llm.span_id]
    assert llm.span_id in downstream_of(trace, tool.span_id)


def test_downstream_is_transitive(healthy):
    retriever = healthy.stage_spans(Stage.RETRIEVAL)[0]
    reached = downstream_of(healthy, retriever.span_id)
    names = {healthy.span(sid).name for sid in reached}

    assert {"prompt-builder", "llm"} <= names
    assert "preprocess" not in names


def test_the_first_span_depends_on_nothing(healthy):
    first = healthy.ordered_spans()[0]
    assert direct_dependencies(healthy)[first.span_id] == set()


# -- stage transitions ---------------------------------------------------


def test_a_clean_handoff_reports_no_problems():
    factory = TraceFactory()
    a = factory.add("retriever", Stage.RETRIEVAL, outputs={"documents": [CURRENT_POLICY]})
    b = factory.add(
        "prompt-builder",
        Stage.PROMPT_BUILD,
        inputs={"documents": [CURRENT_POLICY]},
        outputs={"prompt": "..."},
    )
    factory.finish()

    assert validate_stage_transition(a, b) == []


def test_a_handoff_that_lost_the_payload_is_reported():
    factory = TraceFactory()
    a = factory.add("retriever", Stage.RETRIEVAL, outputs={"documents": [CURRENT_POLICY]})
    b = factory.add("prompt-builder", Stage.PROMPT_BUILD, inputs={"query": "unrelated text"})
    factory.finish()

    problems = validate_stage_transition(a, b)
    assert any("does not appear" in p or "not carried forward" in p for p in problems)


def test_an_unverifiable_handoff_says_so():
    factory = TraceFactory()
    a = factory.add("retriever", Stage.RETRIEVAL, outputs={"documents": [CURRENT_POLICY]})
    b = factory.add("prompt-builder", Stage.PROMPT_BUILD, outputs={"prompt": "..."})
    factory.finish()

    assert any("cannot be verified" in p for p in validate_stage_transition(a, b))


# -- first divergence ----------------------------------------------------


def test_a_healthy_trace_has_no_divergence(healthy):
    report = find_first_divergence(healthy)

    assert report.healthy
    assert report.first_divergence_span_id is None
    assert all(a.verdict is Verdict.HEALTHY for a in report.assessments)


def test_scenario_a_blames_the_retriever(retrieval_failure):
    report = find_first_divergence(retrieval_failure)

    assert report.first_divergence_stage is Stage.RETRIEVAL
    assert report.first_divergence.span_name == "retriever"


def test_scenario_b_blames_the_model(model_failure):
    assert find_first_divergence(model_failure).first_divergence_stage is Stage.LLM


def test_scenario_c_blames_post_processing(postprocessing_failure):
    report = find_first_divergence(postprocessing_failure)

    assert report.first_divergence_stage is Stage.POSTPROCESSING
    # The model must be cleared, not merely outranked.
    llm = next(a for a in report.assessments if a.stage is Stage.LLM)
    assert llm.verdict is Verdict.HEALTHY


def test_downstream_failures_are_labelled_as_consequences(tool_timeout):
    report = find_first_divergence(tool_timeout)

    assert report.first_divergence_stage is Stage.TOOL
    llm = next(a for a in report.assessments if a.stage is Stage.LLM)
    assert llm.verdict is Verdict.DOWNSTREAM_CONSEQUENCE
    assert llm.candidates  # it really did have a problem of its own


def test_an_anomaly_off_the_dependency_path_is_unrelated_not_downstream():
    # A parallel branch that never fed the failing chain must not be folded
    # into the diagnosis.
    factory = TraceFactory()
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION},
        outputs={"documents": []},
    )
    factory.add(
        "prompt-builder",
        Stage.PROMPT_BUILD,
        inputs={"documents": []},
        outputs={"prompt": f"Question: {QUESTION}"},
    )
    trace = factory.finish()
    report = find_first_divergence(trace)

    assert report.first_divergence_stage is Stage.RETRIEVAL
    assert {a.verdict for a in report.assessments} <= {
        Verdict.ROOT_CAUSE_CANDIDATE,
        Verdict.DOWNSTREAM_CONSEQUENCE,
        Verdict.HEALTHY,
    }


def test_a_latency_blip_alone_never_becomes_the_root_cause():
    # Latency is corroborating evidence, not an originating failure. An early
    # slow stage must not outrank a later stage that actually broke.
    factory = TraceFactory()
    factory.add(
        "slow-loader", Stage.DOCUMENT_LOAD, duration_s=5.0, outputs={"documents": [CURRENT_POLICY]}
    )
    factory.add("retriever", Stage.RETRIEVAL, inputs={"query": QUESTION}, outputs={"documents": []})
    report = find_first_divergence(factory.finish())

    assert report.first_divergence_stage is Stage.RETRIEVAL
    loader = next(a for a in report.assessments if a.stage is Stage.DOCUMENT_LOAD)
    assert loader.verdict is not Verdict.ROOT_CAUSE_CANDIDATE
    assert loader.candidates  # still reported, just not blamed


def test_a_structural_anomaly_alone_does_not_count_as_divergence():
    factory = TraceFactory()
    span = factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION},
        outputs={"documents": [CURRENT_POLICY]},
    )
    span.parent_span_id = "f" * 16
    report = find_first_divergence(factory.finish())

    assert report.healthy
    assert "corroborating" in report.explanation


def test_the_earliest_originating_failure_wins_over_a_later_stronger_one():
    factory = TraceFactory()
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION},
        outputs={"documents": [OUTDATED_POLICY]},
    )
    factory.add(
        "llm",
        Stage.LLM,
        inputs={"prompt": "Context: nothing relevant."},
        outputs={"answer": "Returns take 77 days."},
        error=ErrorInfo(type="RuntimeError", message="late failure"),
    )
    report = find_first_divergence(factory.finish())

    # The LLM error is stronger evidence, but it happened later and downstream.
    assert report.first_divergence_stage is Stage.RETRIEVAL


def test_explicit_candidates_can_be_supplied(retrieval_failure):
    candidates = collect_candidates(retrieval_failure)
    report = find_first_divergence(retrieval_failure, candidates=candidates)

    assert report.first_divergence_stage is Stage.RETRIEVAL


def test_collect_candidates_merges_detectors_and_invariants():
    factory = TraceFactory()
    factory.add("preprocess", Stage.PREPROCESSING, outputs={"user_id": "u-1"})
    factory.add("llm", Stage.LLM, outputs={"user_id": "u-2", "answer": "hi"})
    found = collect_candidates(factory.finish())

    assert FailureCategory.INVARIANT_VIOLATION in {c.category for c in found}


# -- ranking -------------------------------------------------------------


def test_the_root_cause_outranks_its_downstream_consequence(tool_timeout):
    ranked = rank_failure_candidates(tool_timeout)

    assert ranked[0].stage is Stage.TOOL
    assert ranked[0].verdict is Verdict.ROOT_CAUSE_CANDIDATE
    llm = next(c for c in ranked if c.stage is Stage.LLM)
    assert ranked[0].score > llm.score


def test_ranks_are_dense_and_ordered(tool_timeout):
    ranked = rank_failure_candidates(tool_timeout)

    assert [c.rank for c in ranked] == list(range(1, len(ranked) + 1))
    assert ranked == sorted(ranked, key=lambda c: -c.score)


def test_score_components_are_recorded_so_the_number_can_be_audited(retrieval_failure):
    top = rank_failure_candidates(retrieval_failure)[0]

    assert set(top.score_components) == {"base", "position", "agreement", "impact"}
    assert top.score_components["position"] == 1.0
    product = 1.0
    for value in top.score_components.values():
        product *= value
    assert top.score == pytest.approx(min(1.0, product), abs=1e-3)


def test_detector_agreement_raises_the_score(retrieval_failure):
    top = rank_failure_candidates(retrieval_failure)[0]

    # Two independent retrieval findings: missing expected document, and stale.
    assert len(top.candidates) >= 2
    assert top.score_components["agreement"] >= 1.0


def test_confidence_falls_when_candidates_compete(tool_timeout, retrieval_failure):
    sole = rank_failure_candidates(retrieval_failure)[0]
    contested = rank_failure_candidates(tool_timeout)[0]

    assert 0.0 < contested.confidence <= 1.0
    assert sole.confidence > 0.0


def test_confidence_is_bounded(model_failure):
    for candidate in rank_failure_candidates(model_failure):
        assert 0.0 <= candidate.confidence <= 1.0
        assert 0.0 <= candidate.score <= 1.0


def test_a_healthy_trace_ranks_nothing(healthy):
    assert rank_failure_candidates(healthy) == []


def test_downstream_effects_are_listed_on_the_root_cause(tool_timeout):
    top = rank_failure_candidates(tool_timeout)[0]

    assert len(top.downstream_effects) == 1
    assert "llm" in top.downstream_effects[0]


# -- the report ----------------------------------------------------------


def test_a_healthy_trace_produces_a_clean_report(healthy):
    report = generate_root_cause_report(healthy)

    assert report.healthy
    assert report.likely_root_cause is None
    assert report.evidence_chain == []
    assert report.recommended_actions == []
    assert report.diagnostic_confidence == 0.0


def test_the_report_names_the_retriever_for_scenario_a(retrieval_failure):
    report = generate_root_cause_report(retrieval_failure)

    assert report.likely_root_cause.stage is Stage.RETRIEVAL
    assert report.first_divergence_stage is Stage.RETRIEVAL
    assert "retriever" in report.summary
    assert report.diagnostic_confidence > 0.5


def test_the_evidence_chain_clears_the_stages_that_behaved(retrieval_failure):
    # The argument that makes the diagnosis convincing: the prompt carried
    # what it was given, and the model answered what it was asked.
    chain = build_evidence_chain(retrieval_failure, find_first_divergence(retrieval_failure))
    exculpatory = [e for e in chain if e.detail.get("role") == "exculpatory"]

    assert {e.stage for e in exculpatory} == {Stage.PROMPT_BUILD, Stage.LLM}
    assert any("unchanged" in e.description for e in exculpatory)
    assert any("consistent with the prompt" in e.description for e in exculpatory)


def test_the_evidence_chain_starts_with_the_cause(retrieval_failure):
    chain = build_evidence_chain(retrieval_failure, find_first_divergence(retrieval_failure))

    assert chain[0].stage is Stage.RETRIEVAL
    assert chain[0].detail.get("role") != "exculpatory"


def test_downstream_failures_appear_in_the_chain_as_consequences(tool_timeout):
    chain = build_evidence_chain(tool_timeout, find_first_divergence(tool_timeout))
    consequences = [e for e in chain if e.detail.get("role") == "downstream consequence"]

    assert len(consequences) == 1
    assert consequences[0].stage is Stage.LLM


def test_every_evidence_item_links_to_a_span(retrieval_failure):
    # Phase 15 requires each evidence item to be clickable through to a span.
    report = generate_root_cause_report(retrieval_failure)

    assert report.evidence_chain
    for item in report.evidence_chain:
        assert item.span_id is not None
        assert retrieval_failure.span(item.span_id) is not None


def test_remediation_matches_the_failure_category(retrieval_failure, tool_timeout):
    assert "retriever" in generate_root_cause_report(retrieval_failure).recommended_actions[0]
    assert "timeout" in generate_root_cause_report(tool_timeout).recommended_actions[0]


def test_the_report_records_its_own_analysis_latency(retrieval_failure):
    report = generate_root_cause_report(retrieval_failure)

    assert report.analysis_ms > 0.0
    assert report.analysis_ms < 1000.0


def test_the_report_round_trips_through_json(retrieval_failure):
    from app.forensics import RootCauseReport

    report = generate_root_cause_report(retrieval_failure)
    restored = RootCauseReport.model_validate_json(report.model_dump_json())

    assert restored.likely_root_cause.stage is Stage.RETRIEVAL
    assert len(restored.evidence_chain) == len(report.evidence_chain)


def test_the_three_scenarios_receive_three_different_diagnoses(
    retrieval_failure, model_failure, postprocessing_failure
):
    # The property the whole system exists to provide, asserted end to end.
    diagnoses = {
        name: generate_root_cause_report(trace).likely_root_cause.stage
        for name, trace in (
            ("retrieval", retrieval_failure),
            ("model", model_failure),
            ("postprocessing", postprocessing_failure),
        )
    }

    assert diagnoses == {
        "retrieval": Stage.RETRIEVAL,
        "model": Stage.LLM,
        "postprocessing": Stage.POSTPROCESSING,
    }
