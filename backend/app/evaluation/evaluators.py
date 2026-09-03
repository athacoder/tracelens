"""Deterministic evaluators (section 20 of CLAUDE.md).

Each returns an :class:`EvaluationResult` carrying a score, the threshold it was
judged against, and the specific items that drove the score. The detail matters
more than the number: "0.4" is not evidence, but "the answer asserts 45 and 90,
neither of which appears in the retrieved context" is.

All five are pure functions of their arguments. No model calls, no network, no
hidden state — so a benchmark number produced by them is reproducible by anyone
running the same trace.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .text import currency_amounts, dates, flatten_text, numbers, overlap

#: Defaults chosen to be defensible, not tuned. Callers override per pipeline.
RELEVANCE_THRESHOLD = 0.30
FAITHFULNESS_THRESHOLD = 0.80
CORRECTNESS_THRESHOLD = 0.90


class EvaluationResult(BaseModel):
    """One measurement, with the observations that produced it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    score: float = Field(ge=0.0, le=1.0)
    threshold: float
    passed: bool
    explanation: str
    detail: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        name: str,
        score: float,
        threshold: float,
        explanation: str,
        **detail: Any,
    ) -> EvaluationResult:
        score = max(0.0, min(1.0, score))
        return cls(
            name=name,
            score=score,
            threshold=threshold,
            passed=score >= threshold,
            explanation=explanation,
            detail=detail,
        )


def evaluate_correctness(
    actual: Any,
    expected: Any,
    threshold: float = CORRECTNESS_THRESHOLD,
) -> EvaluationResult:
    """Does the produced answer match the expected one?

    Compared on three levels, because a benchmark answer that differs only in
    wording is correct while one that differs in a number is not: exact match,
    then agreement of every numeric literal, then content-word overlap.
    """
    actual_text = flatten_text(actual).strip()
    expected_text = flatten_text(expected).strip()

    if not expected_text:
        return EvaluationResult.build(
            "correctness", 0.0, threshold, "no expected answer was supplied"
        )

    if actual_text.casefold() == expected_text.casefold():
        return EvaluationResult.build("correctness", 1.0, threshold, "exact match")

    expected_numbers = numbers(expected_text)
    actual_numbers = numbers(actual_text)
    missing_numbers = sorted(expected_numbers - actual_numbers)
    contradicting = sorted(actual_numbers - expected_numbers)

    word_score = overlap(expected_text, actual_text)
    if missing_numbers or contradicting:
        # A wrong number is a wrong answer, however well the prose matches.
        score = min(word_score, 0.5)
        explanation = (
            f"numeric mismatch: expected {missing_numbers or 'none missing'}, "
            f"answer additionally asserts {contradicting or 'nothing new'}"
        )
    else:
        score = word_score
        explanation = f"no exact match; {word_score:.0%} of the expected wording is present"

    return EvaluationResult.build(
        "correctness",
        score,
        threshold,
        explanation,
        expected=expected_text[:400],
        actual=actual_text[:400],
        missing_numbers=missing_numbers,
        unexpected_numbers=contradicting,
    )


def evaluate_relevance(
    query: str,
    documents: Any,
    threshold: float = RELEVANCE_THRESHOLD,
) -> EvaluationResult:
    """How much of the question does the retrieved material actually address?

    Scored against the best single document as well as the pool: a pile of
    loosely related chunks that jointly mention every query word is not the
    same as retrieving the one document that answers the question.
    """
    items = _as_list(documents)
    if not items:
        return EvaluationResult.build(
            "relevance", 0.0, threshold, "no documents were retrieved", document_count=0
        )

    per_document = [overlap(query, flatten_text(item)) for item in items]
    best = max(per_document)
    pooled = overlap(query, " ".join(flatten_text(item) for item in items))

    return EvaluationResult.build(
        "relevance",
        best,
        threshold,
        f"best document covers {best:.0%} of the question's content words "
        f"({pooled:.0%} pooled across {len(items)})",
        document_count=len(items),
        best_document_score=round(best, 4),
        pooled_score=round(pooled, 4),
        per_document_scores=[round(s, 4) for s in per_document],
    )


def evaluate_faithfulness(
    answer: Any,
    context: Any,
    threshold: float = FAITHFULNESS_THRESHOLD,
) -> EvaluationResult:
    """Is everything the answer asserts present in the material it was given?

    Numbers, dates, and currency amounts are checked individually rather than
    folded into a word score, because those are the assertions that make a
    grounded-looking answer wrong.
    """
    answer_text = flatten_text(answer)
    context_text = flatten_text(context)

    if not answer_text.strip():
        return EvaluationResult.build("faithfulness", 0.0, threshold, "the answer is empty")
    if not context_text.strip():
        return EvaluationResult.build(
            "faithfulness", 0.0, threshold, "no context was supplied to ground the answer against"
        )

    ungrounded_numbers = sorted(numbers(answer_text) - numbers(context_text))
    ungrounded_dates = sorted(dates(answer_text) - dates(context_text))
    ungrounded_amounts = sorted(currency_amounts(answer_text) - currency_amounts(context_text))
    word_grounding = overlap(answer_text, context_text)

    hard_claims = ungrounded_numbers + ungrounded_dates + ungrounded_amounts
    # One ungrounded number is enough to make an answer unfaithful, so the
    # score is capped rather than averaged away by matching prose.
    score = min(word_grounding, 0.4) if hard_claims else word_grounding

    if hard_claims:
        explanation = f"asserts {', '.join(hard_claims[:5])}, absent from the supplied context"
    else:
        explanation = f"{word_grounding:.0%} of the answer's content words appear in the context"

    return EvaluationResult.build(
        "faithfulness",
        score,
        threshold,
        explanation,
        ungrounded_numbers=ungrounded_numbers,
        ungrounded_dates=ungrounded_dates,
        ungrounded_amounts=ungrounded_amounts,
        word_grounding=round(word_grounding, 4),
    )


def evaluate_format(
    value: Any,
    required_fields: list[str] | None = None,
    field_types: dict[str, type | tuple[type, ...]] | None = None,
) -> EvaluationResult:
    """Does the payload have the shape the next stage expects?

    Pass/fail rather than graded: a consumer either finds the field it needs or
    it does not.
    """
    required_fields = required_fields or []
    field_types = field_types or {}

    if not isinstance(value, dict):
        return EvaluationResult.build(
            "format",
            0.0 if required_fields else 1.0,
            1.0,
            f"expected an object, found {type(value).__name__}",
            actual_type=type(value).__name__,
        )

    missing = [field for field in required_fields if field not in value]
    empty = [
        field for field in required_fields if field in value and value[field] in (None, "", [], {})
    ]
    wrong_type = [
        f"{field}: expected {_type_name(expected)}, found {type(value[field]).__name__}"
        for field, expected in field_types.items()
        if field in value and not isinstance(value[field], expected)
    ]

    problems = len(missing) + len(wrong_type)
    checks = max(len(required_fields) + len(field_types), 1)
    score = 1.0 - problems / checks

    parts = []
    if missing:
        parts.append(f"missing {', '.join(missing)}")
    if empty:
        parts.append(f"empty {', '.join(empty)}")
    if wrong_type:
        parts.append("; ".join(wrong_type))

    return EvaluationResult.build(
        "format",
        score,
        1.0,
        "; ".join(parts) if parts else "all required fields present with the expected types",
        missing_fields=missing,
        empty_fields=empty,
        type_errors=wrong_type,
    )


def evaluate_consistency(observations: dict[str, Any]) -> EvaluationResult:
    """Did one value stay the same everywhere it was observed?

    ``observations`` maps a label (usually a stage or span name) to the value
    seen there. Used to check that an identifier, a currency, or a document id
    survived the journey through the pipeline unchanged.
    """
    if len(observations) < 2:
        return EvaluationResult.build(
            "consistency",
            1.0,
            1.0,
            "fewer than two observations; nothing to compare",
            observations=_stringify(observations),
        )

    distinct = {_normalise(v) for v in observations.values()}
    consistent = len(distinct) == 1
    score = 1.0 if consistent else 1.0 / len(distinct)

    return EvaluationResult.build(
        "consistency",
        score,
        1.0,
        "value is identical at every observation point"
        if consistent
        else f"value differs across stages: {_stringify(observations)}",
        distinct_values=sorted(str(v) for v in distinct),
        observations=_stringify(observations),
    )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    if isinstance(value, dict):
        for key in ("documents", "results", "items", "chunks"):
            if key in value:
                return _as_list(value[key])
        return [value]
    return [value]


def _normalise(value: Any) -> Any:
    """Compare values by content, so a list and its tuple are the same value."""
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, list | tuple):
        return tuple(_normalise(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _normalise(v)) for k, v in value.items()))
    return value


def _stringify(observations: dict[str, Any]) -> dict[str, str]:
    return {k: str(v)[:200] for k, v in observations.items()}


def _type_name(expected: type | tuple[type, ...]) -> str:
    if isinstance(expected, tuple):
        return " or ".join(t.__name__ for t in expected)
    return expected.__name__


__all__ = [
    "EvaluationResult",
    "evaluate_consistency",
    "evaluate_correctness",
    "evaluate_faithfulness",
    "evaluate_format",
    "evaluate_relevance",
]
