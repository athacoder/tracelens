"""Phase 6 acceptance: the invariant engine."""

from __future__ import annotations

import pytest
from app.detection.models import EvidenceKind, FailureCategory
from app.invariants import (
    Invariant,
    InvariantRegistry,
    InvariantViolation,
    default_registry,
    explain_violation,
    field_present,
    field_stable,
    numeric_within,
    observations_of,
    retrieved_context_relevant,
    run_invariants,
    tool_results_not_contradicted,
    validate_invariants,
)
from factories import CURRENT_POLICY, QUESTION, TraceFactory
from tracelens.models import ErrorInfo, Severity, Stage


def trace_with_user_ids(*values: str):
    """A trace carrying user_id through preprocessing, retrieval, and the LLM."""
    factory = TraceFactory()
    stages = [Stage.PREPROCESSING, Stage.RETRIEVAL, Stage.LLM]
    for index, (value, stage) in enumerate(zip(values, stages, strict=False)):
        factory.add(f"step-{index}", stage, outputs={"user_id": value, "query": QUESTION})
    return factory.finish()


# -- passing invariants --------------------------------------------------


def test_a_stable_field_produces_no_violation():
    trace = trace_with_user_ids("u-42", "u-42", "u-42")
    assert field_stable("user_id").run(trace) == []


def test_a_field_seen_only_once_cannot_be_unstable():
    # Nothing to compare against is not a violation. Reporting one here would
    # penalise every pipeline that names a field only at its source.
    trace = trace_with_user_ids("u-42")
    assert field_stable("user_id").run(trace) == []


def test_a_field_never_seen_is_not_a_violation():
    assert field_stable("nonexistent").run(trace_with_user_ids("u-42", "u-42")) == []


def test_case_and_whitespace_differences_do_not_break_stability():
    trace = trace_with_user_ids("U-42", " u-42 ")
    assert field_stable("user_id").run(trace) == []


def test_a_healthy_trace_holds_every_default_invariant(healthy):
    assert run_invariants(healthy, default_registry()) == []
    assert validate_invariants(healthy, default_registry())


# -- failing invariants --------------------------------------------------


def test_a_changed_field_is_a_violation():
    trace = trace_with_user_ids("u-42", "u-99")
    violations = field_stable("user_id").run(trace)

    assert len(violations) == 1
    violation = violations[0]
    assert violation.invariant == "user_id_stable"
    assert "u-42" in violation.summary and "u-99" in violation.summary
    assert len(violation.observations) == 2


def test_the_violation_is_blamed_on_the_span_that_changed_the_value():
    trace = trace_with_user_ids("u-42", "u-42", "u-99")
    violation = field_stable("user_id").run(trace)[0]

    culprit = trace.span(violation.span_id)
    assert culprit.name == "step-2"
    assert violation.stage is Stage.LLM


def test_severity_is_carried_through_to_the_violation():
    trace = trace_with_user_ids("u-42", "u-99")
    assert field_stable("user_id", Severity.CRITICAL).run(trace)[0].severity is Severity.CRITICAL
    assert field_stable("user_id", Severity.LOW).run(trace)[0].severity is Severity.LOW


def test_stages_can_scope_where_an_invariant_looks():
    trace = trace_with_user_ids("u-42", "u-42", "u-99")

    assert field_stable("user_id", stages=[Stage.PREPROCESSING, Stage.RETRIEVAL]).run(trace) == []
    assert field_stable("user_id", stages=[Stage.RETRIEVAL, Stage.LLM]).run(trace)


# -- required fields -----------------------------------------------------


def test_a_missing_required_field_is_a_violation():
    factory = TraceFactory()
    factory.add("llm", Stage.LLM, outputs={"answer": "hello"})
    violations = field_present("user_id", [Stage.LLM]).run(factory.finish())

    assert len(violations) == 1
    assert "does not carry 'user_id'" in violations[0].summary
    assert violations[0].stage is Stage.LLM


def test_a_field_present_in_inputs_satisfies_the_invariant():
    factory = TraceFactory()
    factory.add("llm", Stage.LLM, inputs={"user_id": "u-42"}, outputs={"answer": "hi"})
    assert field_present("user_id", [Stage.LLM]).run(factory.finish()) == []


def test_a_field_present_only_in_attributes_also_satisfies_it():
    factory = TraceFactory()
    factory.add("llm", Stage.LLM, outputs={"answer": "hi"}, attributes={"user_id": "u-42"})
    assert field_present("user_id", [Stage.LLM]).run(factory.finish()) == []


# -- numeric ranges ------------------------------------------------------


def test_a_number_below_the_minimum_is_a_violation():
    factory = TraceFactory()
    factory.add("scorer", Stage.VALIDATION, outputs={"score": -0.5})
    violations = numeric_within("score", minimum=0.0, maximum=1.0).run(factory.finish())

    assert len(violations) == 1
    assert violations[0].detail["value"] == -0.5


def test_a_number_above_the_maximum_is_a_violation():
    factory = TraceFactory()
    factory.add("scorer", Stage.VALIDATION, outputs={"score": 1.4})
    assert numeric_within("score", 0.0, 1.0).run(factory.finish())


def test_a_number_inside_the_range_is_not_a_violation():
    factory = TraceFactory()
    factory.add("scorer", Stage.VALIDATION, outputs={"score": 0.5})
    assert numeric_within("score", 0.0, 1.0).run(factory.finish()) == []


def test_a_non_numeric_value_is_skipped_not_crashed_on():
    factory = TraceFactory()
    factory.add("scorer", Stage.VALIDATION, outputs={"score": "high"})
    assert numeric_within("score", 0.0, 1.0).run(factory.finish()) == []


def test_an_open_ended_range_only_checks_the_bound_given():
    factory = TraceFactory()
    factory.add("scorer", Stage.VALIDATION, outputs={"score": 1000.0})
    assert numeric_within("score", minimum=0.0).run(factory.finish()) == []


# -- context relevance ---------------------------------------------------


def test_irrelevant_context_violates_the_relevance_invariant():
    factory = TraceFactory()
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION},
        outputs={"documents": [{"id": "x", "text": "Shipping takes five business days."}]},
    )
    violations = retrieved_context_relevant(0.5).run(factory.finish())

    assert len(violations) == 1
    assert violations[0].detail["threshold"] == 0.5


def test_relevant_context_holds_the_invariant(healthy):
    assert retrieved_context_relevant(0.3).run(healthy) == []


# -- tool contradiction --------------------------------------------------


def test_an_answer_contradicting_a_tool_result_is_a_violation():
    factory = TraceFactory()
    factory.add("order-api", Stage.TOOL, outputs={"result": {"days_remaining": 12}})
    factory.add("llm", Stage.LLM, outputs={"answer": "You have 30 days remaining."})
    violations = tool_results_not_contradicted().run(factory.finish())

    assert len(violations) == 1
    assert "12" in violations[0].summary


def test_an_answer_carrying_the_tool_value_is_not_a_contradiction():
    factory = TraceFactory()
    factory.add("order-api", Stage.TOOL, outputs={"result": {"days_remaining": 12}})
    factory.add("llm", Stage.LLM, outputs={"answer": "You have 12 days remaining."})
    assert tool_results_not_contradicted().run(factory.finish()) == []


def test_a_failed_tool_call_is_not_treated_as_a_source_of_truth():
    factory = TraceFactory()
    factory.add(
        "order-api",
        Stage.TOOL,
        outputs={"result": {"days_remaining": 12}},
        error=ErrorInfo(type="TimeoutError"),
    )
    factory.add("llm", Stage.LLM, outputs={"answer": "You have 30 days remaining."})
    assert tool_results_not_contradicted().run(factory.finish()) == []


def test_a_tool_returning_nothing_numeric_cannot_be_contradicted():
    factory = TraceFactory()
    factory.add("lookup", Stage.TOOL, outputs={"result": {"status": "shipped"}})
    factory.add("llm", Stage.LLM, outputs={"answer": "It shipped 3 days ago."})
    assert tool_results_not_contradicted().run(factory.finish()) == []


# -- multiple violations -------------------------------------------------


def test_several_invariants_can_fail_on_one_trace():
    factory = TraceFactory()
    factory.add("preprocess", Stage.PREPROCESSING, outputs={"user_id": "u-42", "currency": "USD"})
    factory.add("convert", Stage.POSTPROCESSING, outputs={"user_id": "u-99", "currency": "EUR"})
    violations = run_invariants(factory.finish(), default_registry())

    assert {v.invariant for v in violations} == {"user_id_stable", "currency_stable"}


def test_one_invariant_can_report_several_violations():
    factory = TraceFactory()
    factory.add("a", Stage.LLM, outputs={"answer": "x"})
    factory.add("b", Stage.LLM, outputs={"answer": "y"})
    violations = field_present("user_id", [Stage.LLM]).run(factory.finish())

    assert len(violations) == 2


def test_violations_are_ordered_by_the_registry():
    registry = InvariantRegistry([field_stable("currency"), field_stable("user_id")])
    factory = TraceFactory()
    factory.add("a", Stage.PREPROCESSING, outputs={"user_id": "u-1", "currency": "USD"})
    factory.add("b", Stage.POSTPROCESSING, outputs={"user_id": "u-2", "currency": "EUR"})

    assert [v.invariant for v in registry.run(factory.finish())] == [
        "currency_stable",
        "user_id_stable",
    ]


# -- the registry --------------------------------------------------------


def test_registering_and_unregistering():
    registry = InvariantRegistry()
    assert len(registry) == 0

    registry.register(field_stable("user_id"))
    assert "user_id_stable" in registry
    assert len(registry) == 1

    registry.unregister("user_id_stable")
    assert len(registry) == 0


def test_registering_the_same_name_replaces_it():
    registry = InvariantRegistry([field_stable("user_id", Severity.LOW)])
    registry.register(field_stable("user_id", Severity.CRITICAL))

    assert len(registry) == 1
    assert registry.invariants[0].severity is Severity.CRITICAL


def test_a_broken_invariant_does_not_hide_the_working_ones():
    def explode(trace):
        raise RuntimeError("bad rule")

    registry = InvariantRegistry(
        [
            Invariant("broken", "always raises", Severity.HIGH, explode),
            field_stable("user_id"),
        ]
    )
    trace = trace_with_user_ids("u-42", "u-99")
    violations = registry.run(trace)

    assert {v.invariant for v in violations} == {"broken", "user_id_stable"}
    broken = next(v for v in violations if v.invariant == "broken")
    assert "could not be evaluated" in broken.summary
    assert broken.severity is Severity.LOW


def test_unregistering_an_absent_name_is_harmless():
    InvariantRegistry().unregister("nothing")


# -- explanation ---------------------------------------------------------


def test_explain_violation_states_the_rule_and_the_observations():
    trace = trace_with_user_ids("u-42", "u-99")
    text = explain_violation(field_stable("user_id", Severity.CRITICAL).run(trace)[0])

    assert "user_id_stable" in text
    assert "critical" in text
    assert "must hold the same value" in text
    assert "u-42" in text and "u-99" in text


def test_explain_violation_handles_a_violation_with_no_observations():
    violation = InvariantViolation(
        invariant="x", description="d", severity=Severity.LOW, summary="s"
    )
    assert "Observed values" not in explain_violation(violation)


# -- integration with detection ------------------------------------------


def test_a_violation_converts_to_a_ranked_failure_candidate():
    trace = trace_with_user_ids("u-42", "u-99")
    candidate = field_stable("user_id", Severity.CRITICAL).run(trace)[0].to_candidate()

    assert candidate.category is FailureCategory.INVARIANT_VIOLATION
    assert candidate.severity is Severity.CRITICAL
    # Not 1.0: the rule was broken, but a rule can itself be declared wrongly.
    assert candidate.confidence == pytest.approx(0.95)
    assert candidate.evidence[0].kind is EvidenceKind.RULE
    assert candidate.evidence[0].detail["invariant"] == "user_id_stable"


def test_observations_of_finds_a_field_in_inputs_outputs_and_attributes():
    factory = TraceFactory()
    factory.add(
        "step",
        Stage.RETRIEVAL,
        inputs={"user_id": "in"},
        outputs={"user_id": "out"},
        attributes={"user_id": "attr"},
    )
    found = observations_of(factory.finish(), "user_id")

    assert found == {"step.in": "in", "step.out": "out", "step.attr": "attr"}


def test_default_registry_instances_are_independent():
    first, second = default_registry(), default_registry()
    first.unregister("user_id_stable")

    assert "user_id_stable" not in first
    assert "user_id_stable" in second


def test_document_id_corruption_is_caught_by_the_defaults():
    factory = TraceFactory()
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION},
        outputs={"documents": [CURRENT_POLICY], "document_id": "policy-2026"},
    )
    factory.add(
        "prompt-builder",
        Stage.PROMPT_BUILD,
        inputs={"document_id": "policy-2019"},
        outputs={"prompt": "..."},
    )
    violations = run_invariants(factory.finish(), default_registry())

    assert [v.invariant for v in violations] == ["document_id_stable"]
