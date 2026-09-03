"""The failure detection engine (Phase 5).

Eight detectors, each a pure function from a trace to a list of candidates.
They do not talk to each other and they do not rank: a detector reports what it
can see from its own vantage point, and Phase 7/8 decide what that means
together. Keeping them independent is what makes "three detectors independently
flagged the retriever" a meaningful statement.

Confidence follows D-008. A directly observed fact (a span carries an
exception) is 1.0. A deterministic rule violation is 0.5-0.9. A heuristic with
no baseline to compare against stays below 0.5 and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tracelens.models import Severity, Span, Stage, Trace

from ..evaluation.evaluators import (
    RELEVANCE_THRESHOLD,
    evaluate_faithfulness,
    evaluate_relevance,
)
from ..evaluation.text import currency_amounts, dates, flatten_text, numbers
from .models import Evidence, EvidenceKind, FailureCandidate, FailureCategory
from .payloads import (
    answer_of,
    as_datetime,
    document_id,
    document_text,
    documents_of,
    final_answer_span,
    first_of,
    prompt_of,
    query_of,
    source_material,
)


@dataclass(frozen=True)
class StageSchema:
    """What a stage's output must contain for the next stage to work."""

    required: tuple[str, ...] = ()
    #: At least one of these must be present. Used where a stage may name its
    #: result several equally valid ways.
    any_of: tuple[str, ...] = ()
    types: dict[str, type | tuple[type, ...]] = field(default_factory=dict)


#: Deliberately minimal: only contracts that are true of every pipeline of that
#: shape. Anything more opinionated belongs in a per-pipeline schema, passed in
#: by the caller, rather than in a default that produces false positives.
DEFAULT_STAGE_SCHEMAS: dict[Stage, StageSchema] = {
    Stage.RETRIEVAL: StageSchema(required=("documents",), types={"documents": (list, tuple)}),
    Stage.PROMPT_BUILD: StageSchema(any_of=("prompt", "messages")),
    Stage.LLM: StageSchema(any_of=("answer", "text", "completion", "output", "response")),
    Stage.TOOL: StageSchema(any_of=("result", "output", "response", "data")),
}

#: A span is worth questioning on latency when it dominates the trace *and*
#: takes real time. Either alone produces noise: a 2ms span can be 90% of a
#: 2ms trace.
LATENCY_DOMINANCE = 0.60
LATENCY_FLOOR_MS = 500.0
LATENCY_BASELINE_MULTIPLIER = 3.0


# -- 1. execution failures ------------------------------------------------


def detect_execution_failure(trace: Trace) -> list[FailureCandidate]:
    """Spans that reported an error. The only detector that can be certain.

    An exception on a span is not an inference: the pipeline itself said this
    step failed. Confidence 1.0 here means "this was observed", not "this is
    the root cause" — a downstream span erroring because its input was empty
    is equally observed and equally not the cause.
    """
    candidates = []
    for span in trace.ordered_spans():
        if not span.failed:
            continue
        detail: dict[str, Any] = {"status": span.status.value}
        if span.error is not None:
            detail |= {"error_type": span.error.type, "error_message": span.error.message}
            description = f"{span.name} raised {span.error.type}: {span.error.message}"
        else:
            description = f"{span.name} was marked as failed without an attached error"

        candidates.append(
            FailureCandidate(
                detector="detect_execution_failure",
                category=FailureCategory.EXECUTION_ERROR,
                severity=Severity.CRITICAL,
                confidence=1.0,
                summary=description,
                span_id=span.span_id,
                stage=span.stage,
                evidence=[
                    Evidence(
                        kind=EvidenceKind.OBSERVED,
                        description=description,
                        span_id=span.span_id,
                        stage=span.stage,
                        detail=detail,
                    )
                ],
            )
        )
    return candidates


# -- 2. schema violations -------------------------------------------------


def validate_schema(
    trace: Trace,
    schemas: dict[Stage, StageSchema] | None = None,
) -> list[FailureCandidate]:
    """Payloads that do not have the shape the next stage needs.

    A span may declare its own contract through the ``expects_outputs``
    attribute, which overrides the stage default. That is the escape hatch that
    keeps the defaults conservative.
    """
    schemas = schemas or DEFAULT_STAGE_SCHEMAS
    candidates = []

    for span in trace.ordered_spans():
        schema = _schema_for(span, schemas)
        if schema is None:
            continue
        # A span that errored has no obligation to have produced output; the
        # execution detector already owns that failure.
        if span.failed or span.is_open:
            continue

        problems: list[str] = []
        detail: dict[str, Any] = {}

        missing = [f for f in schema.required if f not in span.outputs]
        if missing:
            problems.append(f"missing required output field(s): {', '.join(missing)}")
            detail["missing_fields"] = missing

        if schema.any_of and not any(f in span.outputs for f in schema.any_of):
            problems.append(f"produced none of: {', '.join(schema.any_of)}")
            detail["expected_any_of"] = list(schema.any_of)

        wrong_types = [
            f"{name} is {type(span.outputs[name]).__name__}, expected {_type_name(expected)}"
            for name, expected in schema.types.items()
            if name in span.outputs and not isinstance(span.outputs[name], expected)
        ]
        if wrong_types:
            problems.append("; ".join(wrong_types))
            detail["type_errors"] = wrong_types

        if not problems:
            continue

        detail["actual_fields"] = sorted(span.outputs)
        summary = f"{span.name} output violates its contract: {'; '.join(problems)}"
        candidates.append(
            FailureCandidate(
                detector="validate_schema",
                category=FailureCategory.SCHEMA_VIOLATION,
                severity=Severity.HIGH,
                confidence=0.9,
                summary=summary,
                span_id=span.span_id,
                stage=span.stage,
                evidence=[
                    Evidence(
                        kind=EvidenceKind.RULE,
                        description=summary,
                        span_id=span.span_id,
                        stage=span.stage,
                        detail=detail,
                    )
                ],
            )
        )
    return candidates


def _schema_for(span: Span, schemas: dict[Stage, StageSchema]) -> StageSchema | None:
    declared = span.attributes.get("expects_outputs")
    if isinstance(declared, list | tuple) and declared:
        return StageSchema(required=tuple(str(f) for f in declared))
    return schemas.get(span.stage)


def _type_name(expected: type | tuple[type, ...]) -> str:
    if isinstance(expected, tuple):
        return " or ".join(t.__name__ for t in expected)
    return expected.__name__


# -- 3. missing information ----------------------------------------------


def detect_missing_information(trace: Trace) -> list[FailureCandidate]:
    """Required content that is present in name but empty in substance.

    Two distinct failures live here. A stage that emits ``documents: []`` has
    produced a structurally valid payload with nothing in it. And a stage that
    was handed context but built a prompt containing none of it has silently
    dropped the pipeline's evidence on the floor — the failure that makes an
    otherwise well-behaved model answer from memory.
    """
    candidates = []
    ordered = trace.ordered_spans()

    for span in ordered:
        if span.failed or span.is_open:
            continue
        empty = [k for k, v in span.outputs.items() if v in ([], {}, "")]
        if empty:
            summary = f"{span.name} produced empty {', '.join(sorted(empty))}"
            candidates.append(
                FailureCandidate(
                    detector="detect_missing_information",
                    category=FailureCategory.MISSING_INFORMATION,
                    severity=Severity.HIGH if span.stage is Stage.RETRIEVAL else Severity.MEDIUM,
                    confidence=0.85,
                    summary=summary,
                    span_id=span.span_id,
                    stage=span.stage,
                    evidence=[
                        Evidence(
                            kind=EvidenceKind.OBSERVED,
                            description=summary,
                            span_id=span.span_id,
                            stage=span.stage,
                            detail={"empty_fields": sorted(empty)},
                        )
                    ],
                )
            )

        declared = span.attributes.get("required_fields")
        if isinstance(declared, list | tuple):
            absent = [str(f) for f in declared if f not in span.outputs]
            if absent:
                summary = (
                    f"{span.name} did not carry declared required field(s): {', '.join(absent)}"
                )
                candidates.append(
                    FailureCandidate(
                        detector="detect_missing_information",
                        category=FailureCategory.MISSING_INFORMATION,
                        severity=Severity.HIGH,
                        confidence=0.9,
                        summary=summary,
                        span_id=span.span_id,
                        stage=span.stage,
                        evidence=[
                            Evidence(
                                kind=EvidenceKind.RULE,
                                description=summary,
                                span_id=span.span_id,
                                stage=span.stage,
                                detail={"declared": list(declared), "absent": absent},
                            )
                        ],
                    )
                )

    candidates.extend(_detect_dropped_context(trace, ordered))
    return candidates


def _detect_dropped_context(trace: Trace, ordered: list[Span]) -> list[FailureCandidate]:
    """Retrieved documents that never reached the prompt."""
    retrieved = [d for s in ordered if s.stage is Stage.RETRIEVAL for d in documents_of(s)]
    if not retrieved:
        return []

    prompt_spans = [s for s in ordered if s.stage is Stage.PROMPT_BUILD]
    candidates = []
    for span in prompt_spans:
        prompt = prompt_of(span)
        if not prompt:
            continue
        carried = [d for d in retrieved if _document_appears_in(d, prompt)]
        if carried:
            continue
        summary = (
            f"{span.name} built a prompt containing none of the {len(retrieved)} "
            f"retrieved document(s)"
        )
        candidates.append(
            FailureCandidate(
                detector="detect_missing_information",
                category=FailureCategory.MISSING_INFORMATION,
                severity=Severity.HIGH,
                confidence=0.75,
                summary=summary,
                span_id=span.span_id,
                stage=span.stage,
                evidence=[
                    Evidence(
                        kind=EvidenceKind.COMPARISON,
                        description=summary,
                        span_id=span.span_id,
                        stage=span.stage,
                        detail={
                            "retrieved_document_ids": [document_id(d) for d in retrieved],
                            "prompt_excerpt": prompt[:400],
                        },
                    )
                ],
            )
        )
    return candidates


def _document_appears_in(document: Any, text: str) -> bool:
    """Whether a document reached ``text``, by id or by a distinctive phrase."""
    doc_id = document_id(document)
    if doc_id and doc_id in text:
        return True
    body = document_text(document)
    if not body:
        return False
    # Match on a run of words rather than the whole body, which is usually
    # truncated or reformatted by the time it reaches a prompt.
    words = body.split()
    probe = " ".join(words[:8])
    return bool(probe) and probe.lower() in text.lower()


# -- 4. latency anomalies -------------------------------------------------


def detect_latency_anomaly(
    trace: Trace,
    baselines: dict[Stage, float] | None = None,
    dominance: float = LATENCY_DOMINANCE,
    floor_ms: float = LATENCY_FLOOR_MS,
) -> list[FailureCandidate]:
    """Stages that took long enough to be worth asking about.

    Without historical baselines this is genuinely weak evidence, and the
    confidence says so (0.35). Given per-stage baselines it becomes a real
    comparison and is scored accordingly. Latency alone is almost never a root
    cause; its value is corroborating a timeout or a retry storm found by
    another detector.
    """
    candidates = []
    trace_ms = trace.duration_ms

    for span in trace.ordered_spans():
        duration = span.duration_ms
        if duration is None:
            continue

        baseline = (baselines or {}).get(span.stage)
        if baseline is not None and baseline > 0:
            if duration <= baseline * LATENCY_BASELINE_MULTIPLIER:
                continue
            summary = (
                f"{span.name} took {duration:.0f}ms against a {baseline:.0f}ms baseline "
                f"({duration / baseline:.1f}x)"
            )
            kind, confidence, severity = EvidenceKind.COMPARISON, 0.6, Severity.MEDIUM
            detail = {"duration_ms": duration, "baseline_ms": baseline}
        else:
            if duration < floor_ms or not trace_ms or duration < dominance * trace_ms:
                continue
            share = duration / trace_ms
            summary = (
                f"{span.name} accounts for {share:.0%} of the {trace_ms:.0f}ms trace "
                f"({duration:.0f}ms), with no baseline to compare against"
            )
            kind, confidence, severity = EvidenceKind.HEURISTIC, 0.35, Severity.LOW
            detail = {"duration_ms": duration, "trace_duration_ms": trace_ms, "share": share}

        candidates.append(
            FailureCandidate(
                detector="detect_latency_anomaly",
                category=FailureCategory.LATENCY_ANOMALY,
                severity=severity,
                confidence=confidence,
                summary=summary,
                span_id=span.span_id,
                stage=span.stage,
                evidence=[
                    Evidence(
                        kind=kind,
                        description=summary,
                        span_id=span.span_id,
                        stage=span.stage,
                        detail=detail,
                    )
                ],
            )
        )
    return candidates


# -- 5. semantic inconsistency -------------------------------------------


def detect_semantic_inconsistency(trace: Trace) -> list[FailureCandidate]:
    """Output that contradicts the input the same stage was given.

    Two shapes. A model whose answer asserts facts absent from its own prompt
    is inconsistent with its evidence even if the evidence was perfect — that
    is the model-level failure. A post-processor that changes a number it was
    handed is inconsistent with its input by definition — that is the
    post-processing failure. Separating them is what lets the diagnosis for
    scenario B differ from scenario C.
    """
    candidates: list[FailureCandidate] = []

    for span in trace.ordered_spans():
        if span.failed or span.is_open:
            continue
        if span.stage is Stage.LLM:
            candidates.extend(_llm_inconsistency(span))
        elif span.stage in (Stage.POSTPROCESSING, Stage.VALIDATION):
            candidates.extend(_transformation_inconsistency(span))

    return candidates


def _llm_inconsistency(span: Span) -> list[FailureCandidate]:
    answer = answer_of(span)
    context = prompt_of(span) or flatten_text(span.inputs)
    if not answer or not context:
        return []

    result = evaluate_faithfulness(answer, context)
    if result.passed:
        return []

    ungrounded = (
        result.detail.get("ungrounded_numbers", [])
        + result.detail.get("ungrounded_dates", [])
        + result.detail.get("ungrounded_amounts", [])
    )
    summary = f"{span.name} produced an answer unsupported by its own prompt: {result.explanation}"
    return [
        FailureCandidate(
            detector="detect_semantic_inconsistency",
            category=FailureCategory.SEMANTIC_INCONSISTENCY,
            severity=Severity.HIGH if ungrounded else Severity.MEDIUM,
            # A specific ungrounded number is far stronger evidence than a low
            # word-overlap score, which paraphrase alone can produce.
            confidence=0.8 if ungrounded else 0.45,
            summary=summary,
            span_id=span.span_id,
            stage=span.stage,
            evidence=[
                Evidence(
                    kind=EvidenceKind.COMPARISON if ungrounded else EvidenceKind.HEURISTIC,
                    description=summary,
                    span_id=span.span_id,
                    stage=span.stage,
                    detail={
                        "faithfulness_score": result.score,
                        "ungrounded_claims": ungrounded,
                        "answer_excerpt": answer[:400],
                    },
                )
            ],
        )
    ]


def _transformation_inconsistency(span: Span) -> list[FailureCandidate]:
    """A stage that altered a value it was only supposed to pass through."""
    before = flatten_text(span.inputs)
    after = flatten_text(span.outputs)
    if not before or not after:
        return []

    findings: list[str] = []
    detail: dict[str, Any] = {}
    for label, extract in (
        ("number", numbers),
        ("date", dates),
        ("amount", currency_amounts),
    ):
        incoming, outgoing = extract(before), extract(after)
        dropped = sorted(incoming - outgoing)
        introduced = sorted(outgoing - incoming)
        # Both sides non-empty means a value was replaced, not merely removed
        # (a summariser legitimately drops detail) or added (a formatter may
        # add a total).
        if dropped and introduced:
            findings.append(f"{label} {', '.join(dropped)} became {', '.join(introduced)}")
            detail[f"{label}s_dropped"] = dropped
            detail[f"{label}s_introduced"] = introduced

    if not findings:
        return []

    summary = f"{span.name} altered a value it received: {'; '.join(findings)}"
    detail |= {"input_excerpt": before[:300], "output_excerpt": after[:300]}
    return [
        FailureCandidate(
            detector="detect_semantic_inconsistency",
            category=FailureCategory.SEMANTIC_INCONSISTENCY,
            severity=Severity.HIGH,
            confidence=0.75,
            summary=summary,
            span_id=span.span_id,
            stage=span.stage,
            evidence=[
                Evidence(
                    kind=EvidenceKind.COMPARISON,
                    description=summary,
                    span_id=span.span_id,
                    stage=span.stage,
                    detail=detail,
                )
            ],
        )
    ]


# -- 6. retrieval failures ------------------------------------------------


def detect_retrieval_failure(
    trace: Trace,
    relevance_threshold: float = RELEVANCE_THRESHOLD,
) -> list[FailureCandidate]:
    """Retrieval that returned nothing, the wrong thing, or a stale thing.

    Staleness deserves its own check because it is the failure that looks most
    like success: the pipeline retrieves a real, on-topic, well-formed document
    and every downstream stage behaves perfectly while producing a wrong
    answer. Nothing except the document's own metadata reveals it.
    """
    candidates: list[FailureCandidate] = []

    for span in trace.ordered_spans():
        if span.stage is not Stage.RETRIEVAL or span.failed or span.is_open:
            continue

        documents = documents_of(span)
        query = query_of(span)

        if not documents:
            summary = f"{span.name} returned no documents"
            candidates.append(
                _retrieval_candidate(
                    span,
                    summary,
                    Severity.CRITICAL,
                    0.9,
                    EvidenceKind.OBSERVED,
                    {"query": query, "document_count": 0},
                )
            )
            continue

        candidates.extend(_expected_document_missing(span, documents))
        candidates.extend(_stale_documents(trace, span, documents))

        if query:
            result = evaluate_relevance(query, documents, threshold=relevance_threshold)
            if not result.passed:
                summary = (
                    f"{span.name} returned {len(documents)} document(s) that do not address "
                    f"the query: {result.explanation}"
                )
                candidates.append(
                    _retrieval_candidate(
                        span,
                        summary,
                        Severity.HIGH,
                        0.65,
                        EvidenceKind.COMPARISON,
                        {
                            "query": query,
                            "relevance_score": result.score,
                            "threshold": relevance_threshold,
                            "document_ids": [document_id(d) for d in documents],
                        },
                    )
                )

    return candidates


def _expected_document_missing(span: Span, documents: list[Any]) -> list[FailureCandidate]:
    """A pipeline that declared what it needed, and did not get it."""
    expected = first_of(span.inputs, ("expected_document_id", "expected_doc_id")) or (
        span.attributes.get("expected_document_id")
    )
    if not expected:
        return []
    expected_ids = {
        str(e) for e in (expected if isinstance(expected, list | tuple) else [expected])
    }
    retrieved_ids = {document_id(d) for d in documents} - {None}
    absent = sorted(expected_ids - retrieved_ids)
    if not absent:
        return []

    summary = (
        f"{span.name} did not return the expected document(s) {', '.join(absent)}; "
        f"it returned {', '.join(sorted(str(i) for i in retrieved_ids)) or 'none'}"
    )
    return [
        _retrieval_candidate(
            span,
            summary,
            Severity.CRITICAL,
            0.95,
            EvidenceKind.RULE,
            {"expected": sorted(expected_ids), "retrieved": sorted(str(i) for i in retrieved_ids)},
        )
    ]


def _stale_documents(trace: Trace, span: Span, documents: list[Any]) -> list[FailureCandidate]:
    """Documents whose own metadata says they should not have been used."""
    stale: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        reason = None
        if document.get("superseded_by"):
            reason = f"superseded by {document['superseded_by']}"
        elif str(document.get("status", "")).lower() in {"outdated", "archived", "superseded"}:
            reason = f"status is {document['status']}"
        else:
            expiry = as_datetime(document.get("valid_until") or document.get("expires_at"))
            if expiry is not None and expiry < trace.start_time:
                reason = f"expired on {expiry.date().isoformat()}"
        if reason:
            stale.append({"id": document_id(document), "reason": reason})

    if not stale:
        return []

    summary = f"{span.name} returned stale document(s): " + "; ".join(
        f"{s['id']} ({s['reason']})" for s in stale
    )
    return [
        _retrieval_candidate(
            span,
            summary,
            Severity.CRITICAL,
            0.85,
            EvidenceKind.RULE,
            {"stale_documents": stale, "document_count": len(documents)},
        )
    ]


def _retrieval_candidate(
    span: Span,
    summary: str,
    severity: Severity,
    confidence: float,
    kind: EvidenceKind,
    detail: dict[str, Any],
) -> FailureCandidate:
    return FailureCandidate(
        detector="detect_retrieval_failure",
        category=FailureCategory.RETRIEVAL_FAILURE,
        severity=severity,
        confidence=confidence,
        summary=summary,
        span_id=span.span_id,
        stage=span.stage,
        evidence=[
            Evidence(
                kind=kind,
                description=summary,
                span_id=span.span_id,
                stage=span.stage,
                detail=detail,
            )
        ],
    )


# -- 7. unsupported claims ------------------------------------------------


def detect_unsupported_claims(trace: Trace) -> list[FailureCandidate]:
    """Assertions in the final answer that no source material supports.

    Distinct from semantic inconsistency, which compares a model's answer to
    its own prompt. This compares what the *user* received to everything the
    pipeline actually retrieved or fetched, so it still fires when the claim
    was introduced after the model ran.
    """
    span = final_answer_span(trace)
    if span is None:
        return []
    answer = answer_of(span)
    sources = source_material(trace)
    if not answer or not sources:
        return []

    result = evaluate_faithfulness(answer, sources)
    unsupported = (
        result.detail.get("ungrounded_numbers", [])
        + result.detail.get("ungrounded_dates", [])
        + result.detail.get("ungrounded_amounts", [])
    )
    if not unsupported:
        return []

    summary = (
        f"the final answer asserts {', '.join(unsupported[:5])}, which appears in no "
        f"retrieved document or tool result"
    )
    return [
        FailureCandidate(
            detector="detect_unsupported_claims",
            category=FailureCategory.UNSUPPORTED_CLAIM,
            severity=Severity.HIGH,
            confidence=0.7,
            summary=summary,
            span_id=span.span_id,
            stage=span.stage,
            evidence=[
                Evidence(
                    kind=EvidenceKind.COMPARISON,
                    description=summary,
                    span_id=span.span_id,
                    stage=span.stage,
                    detail={
                        "unsupported": unsupported,
                        "answer_excerpt": answer[:400],
                        "faithfulness_score": result.score,
                    },
                )
            ],
        )
    ]


# -- 8. structural anomalies ---------------------------------------------


def detect_structural_anomaly(trace: Trace) -> list[FailureCandidate]:
    """Problems with the trace itself rather than with the pipeline.

    Worth reporting separately: a span that never closed or references a
    missing parent means the instrumentation is incomplete, so an absence of
    other findings should not be read as a healthy run.
    """
    problems = trace.structural_errors()
    if not problems:
        return []
    summary = f"the trace is structurally incomplete: {problems[0]}"
    if len(problems) > 1:
        summary += f" (and {len(problems) - 1} more)"
    return [
        FailureCandidate(
            detector="detect_structural_anomaly",
            category=FailureCategory.STRUCTURAL_ANOMALY,
            severity=Severity.LOW,
            confidence=0.9,
            summary=summary,
            span_id=None,
            stage=Stage.OTHER,
            evidence=[
                Evidence(
                    kind=EvidenceKind.OBSERVED,
                    description=summary,
                    detail={"problems": problems},
                )
            ],
        )
    ]
