"""Phase 9 and 13 acceptance: the semantic layer and the replay engine."""

from __future__ import annotations

import pytest
from app.forensics import (
    MockProvider,
    SemanticAnalysis,
    analyse_semantically,
    build_brief,
    generate_root_cause_report,
    get_provider,
)
from app.forensics.llm import AnthropicProvider, LLMProvider
from app.services.replay import (
    compare_runs,
    original_inputs,
    replay_trace,
)
from factories import CURRENT_POLICY, QUESTION, TraceFactory, retrieval_failure_trace
from tracelens.models import ErrorInfo, SpanStatus, Stage

# -- the brief -----------------------------------------------------------


def test_the_brief_carries_the_deterministic_finding(retrieval_failure):
    brief = build_brief(generate_root_cause_report(retrieval_failure))

    assert "first_divergence: retrieval" in brief
    assert "== evidence ==" in brief
    assert "== ranked candidates ==" in brief
    assert "retriever" in brief


def test_the_brief_contains_only_evidence_not_the_environment(retrieval_failure):
    # Section 9: the model receives evidence, not unrestricted access. Nothing
    # in the brief should let it reach beyond what the engine already extracted.
    brief = build_brief(generate_root_cause_report(retrieval_failure))

    assert "DATABASE_URL" not in brief
    assert "sqlite" not in brief.lower()
    assert "api_key" not in brief.lower()


def test_the_brief_caps_how_much_evidence_it_includes(retrieval_failure):
    report = generate_root_cause_report(retrieval_failure)
    short = build_brief(report, max_evidence=1)

    assert len(short) < len(build_brief(report, max_evidence=50))


# -- the mock provider ---------------------------------------------------


def test_the_default_provider_is_the_mock():
    provider = get_provider()

    assert isinstance(provider, MockProvider)
    assert isinstance(provider, LLMProvider)


def test_an_unknown_provider_name_falls_back_rather_than_raising():
    # A typo in an environment variable must not take the forensic API down.
    assert isinstance(get_provider("not-a-provider"), MockProvider)


def test_the_anthropic_provider_is_selectable_by_name():
    assert isinstance(get_provider("anthropic"), AnthropicProvider)


def test_the_mock_restates_the_deterministic_finding(retrieval_failure):
    report = generate_root_cause_report(retrieval_failure)
    result = analyse_semantically(report, MockProvider())

    assert result.analysis.likely_root_cause == "retrieval"
    assert result.analysis.confidence == pytest.approx(report.diagnostic_confidence)
    assert result.analysis.evidence
    assert result.analysis.recommended_fix


def test_the_mock_admits_it_called_no_model(retrieval_failure):
    # A stub that invented plausible prose would look like it was working while
    # contributing nothing checkable.
    result = analyse_semantically(generate_root_cause_report(retrieval_failure), MockProvider())

    assert "no language model was called" in result.analysis.reasoning_summary


def test_the_result_records_provider_model_and_prompt_version(retrieval_failure):
    # Section 30: never rely on a human remembering which model and prompt ran.
    result = analyse_semantically(generate_root_cause_report(retrieval_failure), MockProvider())

    assert result.provider == "mock"
    assert result.model == "deterministic-restatement"
    assert result.prompt_version == "forensic-v1"
    assert result.generated_at is not None
    assert result.latency_ms >= 0.0


# -- provider failure and disagreement -----------------------------------


class BrokenProvider:
    name = "broken"
    model = "none"

    def analyse(self, brief: str) -> SemanticAnalysis:
        raise ConnectionError("provider unreachable")


class DisagreeingProvider:
    name = "contrarian"
    model = "test"

    def analyse(self, brief: str) -> SemanticAnalysis:
        return SemanticAnalysis(
            likely_root_cause="the model generation step invented the answer",
            confidence=0.9,
            reasoning_summary="disagrees on purpose",
        )


class AgreeingProvider:
    name = "agreeable"
    model = "test"

    def analyse(self, brief: str) -> SemanticAnalysis:
        return SemanticAnalysis(
            likely_root_cause="the retriever returned a superseded document",
            confidence=0.9,
            reasoning_summary="agrees, in prose",
        )


def test_a_provider_failure_does_not_lose_the_deterministic_diagnosis(retrieval_failure):
    report = generate_root_cause_report(retrieval_failure)
    result = analyse_semantically(report, BrokenProvider())

    assert result.error is not None
    assert "ConnectionError" in result.error
    assert result.analysis.likely_root_cause == "retrieval"
    assert result.analysis.confidence == 0.0
    assert not result.trustworthy


def test_a_disagreement_is_recorded_not_silently_accepted(retrieval_failure):
    # The deterministic engine stays authoritative. A model naming a different
    # stage is a signal that the evidence is ambiguous, not an overwrite.
    result = analyse_semantically(
        generate_root_cause_report(retrieval_failure), DisagreeingProvider()
    )

    assert result.disagrees_with_deterministic
    assert not result.trustworthy
    assert result.deterministic_stage is Stage.RETRIEVAL


def test_prose_that_agrees_is_not_counted_as_disagreement(retrieval_failure):
    # "the retriever returned a superseded document" agrees with `retrieval`
    # even though it never writes the word.
    result = analyse_semantically(generate_root_cause_report(retrieval_failure), AgreeingProvider())

    assert not result.disagrees_with_deterministic
    assert result.trustworthy


def test_a_healthy_trace_cannot_disagree(healthy):
    result = analyse_semantically(generate_root_cause_report(healthy), DisagreeingProvider())

    assert not result.disagrees_with_deterministic


def test_the_structured_output_schema_bounds_confidence():
    with pytest.raises(ValueError):
        SemanticAnalysis(likely_root_cause="x", confidence=1.5, reasoning_summary="y")


def test_the_result_round_trips_through_json(retrieval_failure):
    from app.forensics import SemanticForensicResult

    result = analyse_semantically(generate_root_cause_report(retrieval_failure), MockProvider())
    restored = SemanticForensicResult.model_validate_json(result.model_dump_json())

    assert restored.analysis.likely_root_cause == result.analysis.likely_root_cause
    assert restored.provider == "mock"


# -- replay: comparing runs ----------------------------------------------


def build_run(answer: str = "30 days", *, slow: bool = False, error: bool = False):
    factory = TraceFactory("rag-run")
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION},
        outputs={"documents": [CURRENT_POLICY]},
    )
    factory.add(
        "llm",
        Stage.LLM,
        duration_s=2.0 if slow else 0.05,
        inputs={"prompt": "Context: ..."},
        outputs={"answer": answer},
        error=ErrorInfo(type="TimeoutError") if error else None,
    )
    return factory.finish()


def test_two_identical_runs_compare_as_identical():
    comparison = compare_runs(build_run(), build_run())

    assert comparison.identical
    assert comparison.diverged_at is None
    assert comparison.changed_spans == []
    assert "reproduced the original run exactly" in comparison.summary


def test_span_ids_differing_does_not_make_runs_differ():
    # Ids are regenerated every run; matching on them would report two
    # identical runs as sharing nothing.
    first, second = build_run(), build_run()

    assert first.spans[0].span_id != second.spans[0].span_id
    assert compare_runs(first, second).identical


def test_a_changed_output_is_reported_with_its_field():
    comparison = compare_runs(build_run("30 days"), build_run("90 days"))

    assert not comparison.identical
    assert comparison.diverged_at == "llm"
    assert comparison.diverged_stage is Stage.LLM
    diff = comparison.changed_spans[0]
    assert diff.outputs_changed
    assert diff.changed_fields == ["answer"]


def test_the_first_changed_stage_is_the_reported_divergence():
    # Mirrors first-divergence: the earliest change is the one to look at.
    original = build_run()
    replay = build_run("90 days")
    replay.spans[0].outputs = {"documents": []}
    comparison = compare_runs(original, replay)

    assert comparison.diverged_at == "retriever"
    assert len(comparison.changed_spans) == 2
    assert "later stage(s) also differ" in comparison.summary


def test_a_stage_missing_from_the_replay_is_reported():
    replay = build_run()
    replay.spans.pop()
    comparison = compare_runs(build_run(), replay)

    diff = comparison.changed_spans[0]
    assert diff.span_name == "llm"
    assert diff.in_original and not diff.in_replay
    assert "did not run in the replay" in comparison.summary


def test_a_stage_new_in_the_replay_is_reported():
    original = build_run()
    original.spans.pop()
    comparison = compare_runs(original, build_run())

    diff = comparison.changed_spans[0]
    assert not diff.in_original and diff.in_replay
    assert "new in the replay" in comparison.summary


def test_an_error_appearing_in_the_replay_is_reported():
    comparison = compare_runs(build_run(), build_run(error=True))

    diff = next(d for d in comparison.changed_spans if d.span_name == "llm")
    assert diff.error_changed
    assert diff.status_changed


def test_a_latency_regression_without_a_behaviour_change_is_reported_separately():
    comparison = compare_runs(build_run(), build_run(slow=True))

    assert comparison.identical  # behaviour is unchanged
    regressions = comparison.latency_regressions
    assert [d.span_name for d in regressions] == ["llm"]
    assert regressions[0].latency_delta_ms > 1000
    assert "got slower without changing behaviour" in comparison.summary


def test_millisecond_noise_is_not_a_latency_regression():
    original = build_run()
    replay = build_run()
    # 1ms slower on a 50ms stage is noise, not a regression.
    replay.spans[1].end_time = replay.spans[1].end_time.replace(microsecond=51000)

    assert compare_runs(original, replay).latency_regressions == []


def test_volatile_keys_are_ignored_when_comparing():
    original = build_run()
    replay = build_run()
    original.spans[0].outputs["request_id"] = "req-1"
    replay.spans[0].outputs["request_id"] = "req-2"

    assert compare_runs(original, replay).identical


# -- replay: driving a re-run --------------------------------------------


def test_replay_runs_the_supplied_runner_against_the_stored_trace(session):
    from app.services.ingest import ingest_trace

    original = retrieval_failure_trace()
    ingest_trace(session, original, analyse=False)
    session.commit()

    def runner(trace):
        # A runner that reproduces the original exactly.
        return trace.model_copy(deep=True)

    comparison = replay_trace(session, original.trace_id, runner)

    assert comparison is not None
    assert comparison.identical


def test_replay_detects_a_regression_introduced_by_the_runner(session):
    from app.services.ingest import ingest_trace

    original = retrieval_failure_trace()
    ingest_trace(session, original, analyse=False)
    session.commit()

    def broken_runner(trace):
        replayed = trace.model_copy(deep=True)
        replayed.stage_spans(Stage.LLM)[0].outputs = {"answer": "something else entirely"}
        return replayed

    comparison = replay_trace(session, original.trace_id, broken_runner)

    assert not comparison.identical
    assert comparison.diverged_stage is Stage.LLM


def test_replaying_an_unknown_trace_returns_none(session):
    assert replay_trace(session, "f" * 32, lambda t: t) is None


def test_original_inputs_exposes_each_stage_payload():
    trace = retrieval_failure_trace()
    inputs = original_inputs(trace)

    assert inputs["retriever"]["query"] == QUESTION
    assert inputs["retriever"]["expected_document_id"] == "policy-2026"


def test_the_comparison_round_trips_through_json():
    from app.services.replay import RunComparison

    comparison = compare_runs(build_run(), build_run("90 days"))
    restored = RunComparison.model_validate_json(comparison.model_dump_json())

    assert restored.diverged_at == "llm"
    assert len(restored.span_diffs) == len(comparison.span_diffs)


def test_status_only_changes_are_detected():
    original = build_run()
    replay = build_run()
    replay.spans[0].status = SpanStatus.UNSET

    comparison = compare_runs(original, replay)
    assert not comparison.identical
    assert comparison.changed_spans[0].status_changed
