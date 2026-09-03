"""Phases 10-12 acceptance: the broken RAG benchmark and its metrics.

These tests protect the benchmark's integrity as much as its results. A
benchmark that quietly stops injecting a failure, or that starts grading itself
against its own output, would keep reporting a high score while measuring
nothing.
"""

from __future__ import annotations

import json

import pytest
from app.forensics import generate_root_cause_report
from tracelens import MemoryExporter, Tracer, deterministic_ids
from tracelens.models import CaptureMode, Stage

from benchmark.corpus import DOCUMENTS, DOCUMENTS_BY_ID, QUESTIONS
from benchmark.metrics import CaseResult, format_report, score
from benchmark.pipeline import MockLLM, run_pipeline
from benchmark.run import run_benchmark, write_reports
from benchmark.scenarios import (
    FAILURE_SCENARIOS,
    HARD_SCENARIOS,
    SCENARIO_NAMES,
    SCENARIOS,
    get_scenario,
)

#: One question is enough for the structural tests; the accuracy tests run the
#: whole grid.
SAMPLE = QUESTIONS[0]


def fresh_tracer() -> Tracer:
    return Tracer(
        project="benchmark",
        pipeline="rag",
        exporter=MemoryExporter(),
        capture=CaptureMode.FULL,
    )


# -- the corpus ----------------------------------------------------------


def test_every_question_points_at_a_document_that_exists():
    for question in QUESTIONS:
        assert question.expected_document_id in DOCUMENTS_BY_ID
        if question.stale_document_id:
            assert question.stale_document_id in DOCUMENTS_BY_ID


def test_every_expected_answer_appears_in_its_document():
    # If an expected answer is not in the corpus, the benchmark is grading
    # against something the pipeline could never have retrieved.
    for question in QUESTIONS:
        document = DOCUMENTS_BY_ID[question.expected_document_id]
        assert question.expected_answer in document.text


def test_superseded_documents_declare_what_replaced_them():
    for document in DOCUMENTS:
        if document.status == "outdated":
            assert document.superseded_by in DOCUMENTS_BY_ID
            assert document.valid_until


def test_stale_documents_differ_from_their_replacements():
    # Otherwise the stale-retrieval scenario would produce a correct answer.
    for document in DOCUMENTS:
        if document.superseded_by:
            assert document.text != DOCUMENTS_BY_ID[document.superseded_by].text


# -- scenario definitions ------------------------------------------------


def test_the_spec_minimum_scenarios_are_all_present():
    required = {
        "wrong_document",
        "outdated_document",
        "missing_context",
        "context_corruption",
        "prompt_corruption",
        "schema_violation",
        "tool_timeout",
        "wrong_tool_response",
        "unsupported_claim",
        "postprocessing_corruption",
    }
    assert required <= set(SCENARIO_NAMES)


def test_every_failure_scenario_names_a_root_stage():
    for scenario in FAILURE_SCENARIOS:
        assert scenario.root_stage is not None
        assert scenario.ground_truth.failure_type is not None
        assert scenario.ground_truth.expected_behavior


def test_healthy_scenarios_name_no_root_stage():
    for scenario in SCENARIOS:
        if not scenario.failure_present:
            assert scenario.root_stage is None


def test_scenario_names_are_unique():
    assert len(SCENARIO_NAMES) == len(set(SCENARIO_NAMES))


def test_an_unknown_scenario_is_rejected():
    with pytest.raises(KeyError):
        get_scenario("no_such_scenario")


# -- the pipeline --------------------------------------------------------


def test_the_healthy_pipeline_answers_correctly():
    outcome = run_pipeline(SAMPLE, "healthy", tracer=fresh_tracer())
    assert outcome.answer == SAMPLE.expected_answer


def test_the_pipeline_records_every_stage():
    trace = run_pipeline(SAMPLE, "healthy", tracer=fresh_tracer()).trace
    stages = {span.stage for span in trace.spans}

    assert stages == {
        Stage.PREPROCESSING,
        Stage.DOCUMENT_LOAD,
        Stage.CHUNKING,
        Stage.RETRIEVAL,
        Stage.TOOL,
        Stage.PROMPT_BUILD,
        Stage.LLM,
        Stage.POSTPROCESSING,
        Stage.VALIDATION,
    }


def test_the_trace_does_not_reveal_the_scenario_to_the_engine():
    # The scenario name is recorded on the trace for bookkeeping, but no span
    # payload may leak it: the engine has to work the failure out from the
    # data, not read the answer off the fixture.
    trace = run_pipeline(SAMPLE, "outdated_document", tracer=fresh_tracer()).trace

    assert trace.attributes["scenario"] == "outdated_document"
    for span in trace.spans:
        blob = json.dumps([span.inputs, span.outputs, span.attributes], default=str)
        assert "outdated_document" not in blob


def test_the_model_records_its_provider_and_prompt_version():
    # Section 30: never rely on a human remembering which model ran.
    trace = run_pipeline(SAMPLE, "healthy", tracer=fresh_tracer()).trace
    llm = trace.stage_spans(Stage.LLM)[0]

    assert llm.attributes["provider"] == MockLLM.provider
    assert llm.attributes["model"] == MockLLM.model
    assert llm.attributes["prompt_version"] == MockLLM.prompt_version


def test_traces_are_marked_synthetic():
    trace = run_pipeline(SAMPLE, "healthy", tracer=fresh_tracer()).trace
    assert trace.attributes["synthetic"] is True


@pytest.mark.parametrize("scenario", SCENARIO_NAMES)
def test_every_scenario_produces_a_well_formed_trace(scenario):
    trace = run_pipeline(SAMPLE, scenario, tracer=fresh_tracer()).trace

    assert trace.spans
    assert trace.end_time is not None
    assert trace.structural_errors() == []


def test_a_stale_scenario_really_retrieves_the_stale_document():
    trace = run_pipeline(SAMPLE, "outdated_document", tracer=fresh_tracer()).trace
    retrieved = trace.stage_spans(Stage.RETRIEVAL)[0].outputs["documents"]

    assert [d["id"] for d in retrieved] == [SAMPLE.stale_document_id]


def test_the_timeout_scenario_records_the_error_and_keeps_going():
    trace = run_pipeline(SAMPLE, "tool_timeout", tracer=fresh_tracer()).trace
    tool = trace.stage_spans(Stage.TOOL)[0]

    assert tool.error.type == "TimeoutError"
    # The pipeline degrades rather than aborting, so later stages still ran.
    assert trace.stage_spans(Stage.LLM)


def test_the_slow_scenarios_actually_take_time():
    trace = run_pipeline(SAMPLE, "slow_but_correct", tracer=fresh_tracer()).trace
    loader = trace.stage_spans(Stage.DOCUMENT_LOAD)[0]

    assert loader.duration_ms > 500


# -- reproducibility -----------------------------------------------------


def test_two_runs_of_the_same_case_produce_the_same_content():
    first = run_pipeline(SAMPLE, "outdated_document", tracer=fresh_tracer()).trace
    second = run_pipeline(SAMPLE, "outdated_document", tracer=fresh_tracer()).trace

    assert [s.name for s in first.spans] == [s.name for s in second.spans]
    assert first.spans[3].outputs == second.spans[3].outputs


def test_a_seed_makes_trace_ids_reproducible():
    with deterministic_ids(11):
        first = run_pipeline(SAMPLE, "healthy", tracer=fresh_tracer()).trace
    with deterministic_ids(11):
        second = run_pipeline(SAMPLE, "healthy", tracer=fresh_tracer()).trace

    assert first.trace_id == second.trace_id
    assert [s.span_id for s in first.spans] == [s.span_id for s in second.spans]


def test_different_seeds_give_different_ids():
    with deterministic_ids(1):
        first = run_pipeline(SAMPLE, "healthy", tracer=fresh_tracer()).trace
    with deterministic_ids(2):
        second = run_pipeline(SAMPLE, "healthy", tracer=fresh_tracer()).trace

    assert first.trace_id != second.trace_id


# -- diagnosis per scenario ----------------------------------------------


@pytest.mark.parametrize("scenario_name", SCENARIO_NAMES)
def test_each_scenario_is_diagnosed_at_its_injected_stage(scenario_name):
    scenario = get_scenario(scenario_name)
    trace = run_pipeline(SAMPLE, scenario_name, tracer=fresh_tracer()).trace
    report = generate_root_cause_report(trace)

    if scenario.failure_present:
        assert not report.healthy, f"{scenario_name} was not detected at all"
        assert report.first_divergence_stage is scenario.root_stage
    else:
        assert report.healthy, f"{scenario_name} was falsely flagged: {report.summary}"


def test_the_hard_scenarios_are_not_won_by_the_loudest_signal():
    # A slow model and a corrupted post-processor are both real findings in
    # these runs. The earliest cause still has to win.
    for name in sorted(HARD_SCENARIOS):
        scenario = get_scenario(name)
        report = generate_root_cause_report(run_pipeline(SAMPLE, name, tracer=fresh_tracer()).trace)
        assert report.first_divergence_stage is scenario.root_stage, name


def test_the_compound_scenario_still_reports_the_later_fault():
    # Naming retrieval as the root cause must not mean hiding the corrupted
    # post-processor; it is reported, just not blamed.
    report = generate_root_cause_report(
        run_pipeline(SAMPLE, "compound_retrieval_and_postprocessing", tracer=fresh_tracer()).trace
    )
    stages = {c.stage for c in report.ranked_candidates}

    assert report.first_divergence_stage is Stage.RETRIEVAL
    assert Stage.POSTPROCESSING in stages


# -- metrics -------------------------------------------------------------


def make_case(**overrides) -> CaseResult:
    defaults = dict(
        scenario="s",
        question_id="q",
        trace_id="a" * 32,
        failure_present=True,
        expected_stage=Stage.RETRIEVAL,
        detected_failure=True,
        predicted_stage=Stage.RETRIEVAL,
        predicted_confidence=0.8,
        analysis_ms=1.0,
    )
    return CaseResult(**{**defaults, **overrides})


def test_a_perfect_run_scores_perfectly():
    metrics = score(
        [
            make_case(),
            make_case(
                failure_present=False,
                detected_failure=False,
                expected_stage=None,
                predicted_stage=None,
            ),
        ]
    )

    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.false_positive_rate == 0.0
    assert metrics.root_cause_accuracy == 1.0


def test_a_false_positive_lowers_precision_not_recall():
    metrics = score(
        [
            make_case(),
            make_case(failure_present=False, detected_failure=True, expected_stage=None),
        ]
    )

    assert metrics.recall == 1.0
    assert metrics.precision == 0.5
    assert metrics.false_positive_rate == 1.0


def test_a_missed_failure_lowers_recall():
    metrics = score([make_case(), make_case(detected_failure=False, predicted_stage=None)])

    assert metrics.recall == 0.5
    assert metrics.precision == 1.0


def test_detecting_the_wrong_stage_counts_as_detected_but_not_localised():
    # The distinction the whole benchmark rests on: noticing a failure is not
    # the same as finding its cause.
    metrics = score([make_case(predicted_stage=Stage.LLM)])

    assert metrics.detection_rate == 1.0
    assert metrics.root_cause_accuracy == 0.0


def test_healthy_cases_are_excluded_from_localisation_accuracy():
    metrics = score(
        [
            make_case(
                failure_present=False,
                detected_failure=False,
                expected_stage=None,
                predicted_stage=None,
            )
        ]
    )

    assert metrics.failure_cases == 0
    assert metrics.root_cause_accuracy == 0.0  # no failures to localise


def test_metrics_of_an_empty_run_do_not_divide_by_zero():
    metrics = score([])

    assert metrics.f1 == 0.0
    assert metrics.precision == 0.0
    assert metrics.mean_analysis_ms == 0.0


def test_per_class_metrics_cover_every_scenario_seen():
    metrics = score([make_case(scenario="a"), make_case(scenario="b")])

    assert {c.scenario for c in metrics.per_class} == {"a", "b"}


def test_the_report_states_that_the_data_is_synthetic():
    text = format_report(score([make_case()]))

    assert "Synthetic data" in text
    assert "not a calibrated probability" in text
    assert "regression suite" in text


def test_the_report_lists_misattributions_when_there_are_any():
    text = format_report(score([make_case()]), {"s: retrieval -> llm": 2})

    assert "Misattributions" in text
    assert "retrieval -> llm" in text


# -- the full run --------------------------------------------------------


def test_the_full_benchmark_meets_its_accuracy_floor():
    """A regression gate, not a claim about the world.

    The floor is deliberately below the current score: this test exists to
    catch the engine silently getting worse, not to be re-tuned upward every
    time it gets better.
    """
    run = run_benchmark(questions=QUESTIONS[:3])

    assert run.metrics.total_cases == 3 * len(SCENARIO_NAMES)
    assert run.metrics.root_cause_accuracy >= 0.85, run.confusion
    assert run.metrics.false_positive_rate <= 0.10
    assert run.metrics.recall >= 0.95
    assert run.metrics.mean_analysis_ms < 100.0


def test_writing_the_reports_produces_both_formats(tmp_path):
    run = run_benchmark(scenarios=["healthy", "outdated_document"], questions=QUESTIONS[:1])
    text_path, json_path = write_reports(run, tmp_path)

    assert "TraceLens forensic benchmark" in text_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["metrics"]["total_cases"] == 2
    assert len(payload["cases"]) == 2
    assert payload["cases"][0]["expected_stage"] in (None, "retrieval")


def test_the_cli_runs_a_single_scenario(capsys):
    from benchmark.run import main

    exit_code = main(
        ["--scenario", "outdated_document", "--question", "q-refund-window", "--no-write"]
    )

    assert exit_code == 0
    assert "TraceLens forensic benchmark" in capsys.readouterr().out
