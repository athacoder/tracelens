"""Factories for the invariant shapes pipelines actually need.

Writing an invariant by hand means writing a function over a whole trace.
Almost every real rule is one of a handful of shapes — this value must not
change, this field must be present here, this number must stay in range — so
those shapes are parameterised here and the escape hatch (a plain callable)
stays available for anything else.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tracelens.models import Severity, Span, Stage, Trace

from ..detection.payloads import documents_of, final_answer_span, query_of
from ..evaluation.evaluators import evaluate_relevance
from ..evaluation.text import flatten_text, numbers
from .models import Invariant, InvariantViolation


def observations_of(
    trace: Trace, field: str, stages: Iterable[Stage] | None = None
) -> dict[str, Any]:
    """Every value a named field took, keyed by the span that held it.

    Searches inputs, then outputs, then attributes, because a field can enter a
    span one way and leave it another and the invariant cares about both.
    """
    allowed = set(stages) if stages else None
    found: dict[str, Any] = {}
    for span in trace.ordered_spans():
        if allowed is not None and span.stage not in allowed:
            continue
        for where, payload in (
            ("in", span.inputs),
            ("out", span.outputs),
            ("attr", span.attributes),
        ):
            if field in payload:
                found[f"{span.name}.{where}"] = payload[field]
    return found


def _normalise(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, list):
        return tuple(_normalise(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _normalise(v)) for k, v in value.items()))
    return value


def _span_named(trace: Trace, label: str) -> Span | None:
    name = label.rsplit(".", 1)[0]
    return next((s for s in trace.ordered_spans() if s.name == name), None)


def field_stable(
    field: str,
    severity: Severity = Severity.HIGH,
    stages: Iterable[Stage] | None = None,
) -> Invariant:
    """A value must be identical everywhere it appears.

    The workhorse invariant: user_id, document_id, currency, tenant. Silent
    corruption of an identifier mid-pipeline is invisible to every heuristic
    and obvious to this check.
    """
    description = f"'{field}' must hold the same value at every stage that carries it"

    def check(trace: Trace) -> list[InvariantViolation]:
        seen = observations_of(trace, field, stages)
        if len(seen) < 2:
            return []
        distinct = {_normalise(v) for v in seen.values()}
        if len(distinct) == 1:
            return []

        # Blame the first span whose value differs from the first observation,
        # since that is where the change was introduced.
        labels = list(seen)
        baseline = _normalise(seen[labels[0]])
        culprit_label = next(lbl for lbl in labels if _normalise(seen[lbl]) != baseline)
        culprit = _span_named(trace, culprit_label)

        return [
            InvariantViolation(
                invariant=f"{field}_stable",
                description=description,
                severity=severity,
                summary=(
                    f"'{field}' changed during the run: "
                    + " -> ".join(f"{lbl}={seen[lbl]!r}" for lbl in labels)
                ),
                span_id=culprit.span_id if culprit else None,
                stage=culprit.stage if culprit else Stage.OTHER,
                observations=seen,
                detail={"distinct_value_count": len(distinct)},
            )
        ]

    return Invariant(f"{field}_stable", description, severity, check)


def field_present(
    field: str,
    stages: Iterable[Stage],
    severity: Severity = Severity.HIGH,
) -> Invariant:
    """A field must survive into every listed stage."""
    stage_list = list(stages)
    description = f"'{field}' must be present at: {', '.join(s.value for s in stage_list)}"

    def check(trace: Trace) -> list[InvariantViolation]:
        violations = []
        for stage in stage_list:
            for span in trace.stage_spans(stage):
                if field in span.inputs or field in span.outputs or field in span.attributes:
                    continue
                violations.append(
                    InvariantViolation(
                        invariant=f"{field}_present",
                        description=description,
                        severity=severity,
                        summary=f"{span.name} ({stage.value}) does not carry '{field}'",
                        span_id=span.span_id,
                        stage=stage,
                        observations={
                            f"{span.name}.in": sorted(span.inputs),
                            f"{span.name}.out": sorted(span.outputs),
                        },
                    )
                )
        return violations

    return Invariant(f"{field}_present", description, severity, check)


def numeric_within(
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
    severity: Severity = Severity.HIGH,
) -> Invariant:
    """A numeric field must stay inside a declared range."""
    bounds = (
        f"{minimum if minimum is not None else '-inf'}..{maximum if maximum is not None else 'inf'}"
    )
    description = f"'{field}' must stay within {bounds}"

    def check(trace: Trace) -> list[InvariantViolation]:
        violations = []
        for label, value in observations_of(trace, field).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if (minimum is not None and number < minimum) or (
                maximum is not None and number > maximum
            ):
                span = _span_named(trace, label)
                violations.append(
                    InvariantViolation(
                        invariant=f"{field}_within_{bounds}",
                        description=description,
                        severity=severity,
                        summary=f"{label} = {number}, outside the allowed range {bounds}",
                        span_id=span.span_id if span else None,
                        stage=span.stage if span else Stage.OTHER,
                        observations={label: value},
                        detail={"minimum": minimum, "maximum": maximum, "value": number},
                    )
                )
        return violations

    return Invariant(f"{field}_within_{bounds}", description, severity, check)


def retrieved_context_relevant(
    threshold: float = 0.3,
    severity: Severity = Severity.HIGH,
) -> Invariant:
    """Retrieved context must actually address the query it was fetched for."""
    description = f"retrieved documents must cover at least {threshold:.0%} of the query"

    def check(trace: Trace) -> list[InvariantViolation]:
        violations = []
        for span in trace.stage_spans(Stage.RETRIEVAL):
            query = query_of(span)
            documents = documents_of(span)
            if not query or not documents:
                continue
            result = evaluate_relevance(query, documents, threshold=threshold)
            if result.passed:
                continue
            violations.append(
                InvariantViolation(
                    invariant="retrieved_context_relevant",
                    description=description,
                    severity=severity,
                    summary=(
                        f"{span.name} retrieved context scoring {result.score:.2f} "
                        f"against a {threshold:.2f} threshold"
                    ),
                    span_id=span.span_id,
                    stage=Stage.RETRIEVAL,
                    observations={"query": query},
                    detail={"score": result.score, "threshold": threshold},
                )
            )
        return violations

    return Invariant("retrieved_context_relevant", description, severity, check)


def tool_results_not_contradicted(severity: Severity = Severity.HIGH) -> Invariant:
    """A number a tool returned must not be replaced by a different one.

    Distinguishes "the answer omitted the tool result" (legitimate: a model may
    summarise) from "the answer states a different value than the tool
    returned" (a contradiction of a source of truth).
    """
    description = "numeric values returned by tools must not be contradicted downstream"

    def check(trace: Trace) -> list[InvariantViolation]:
        tool_spans = [s for s in trace.stage_spans(Stage.TOOL) if not s.failed]
        answer_span = final_answer_span(trace)
        if not tool_spans or answer_span is None:
            return []

        answer_text = flatten_text(answer_span.outputs)
        answer_numbers = numbers(answer_text)
        violations = []

        for span in tool_spans:
            tool_numbers = numbers(flatten_text(span.outputs))
            if not tool_numbers or tool_numbers & answer_numbers:
                # Either the tool returned nothing numeric, or at least one of
                # its values survived. Neither is a contradiction.
                continue
            violations.append(
                InvariantViolation(
                    invariant="tool_results_not_contradicted",
                    description=description,
                    severity=severity,
                    summary=(
                        f"{answer_span.name} states {', '.join(sorted(answer_numbers)) or 'no'} "
                        f"where {span.name} returned {', '.join(sorted(tool_numbers))}"
                    ),
                    span_id=answer_span.span_id,
                    stage=answer_span.stage,
                    observations={
                        f"{span.name}.out": sorted(tool_numbers),
                        f"{answer_span.name}.out": sorted(answer_numbers),
                    },
                )
            )
        return violations

    return Invariant("tool_results_not_contradicted", description, severity, check)
