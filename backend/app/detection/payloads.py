"""Conventions for reading pipeline payloads out of spans.

Detectors need to find "the query", "the documents", "the answer" in payloads
written by user code that TraceLens does not control. Rather than demand one
rigid schema, each accessor tries the handful of names that are actually used
in practice and returns None when it finds nothing — a detector that cannot
locate its subject must abstain, not guess.

Every convention this module encodes is documented in ``docs/sdk.md`` so an
instrumented pipeline can opt into stronger detection by naming its fields.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tracelens.models import Span, Stage, Trace

from ..evaluation.text import flatten_text

QUERY_KEYS = ("query", "question", "input", "user_input", "prompt", "text")
DOCUMENT_KEYS = ("documents", "docs", "results", "chunks", "context", "passages")
ANSWER_KEYS = ("answer", "text", "completion", "output", "response", "result", "content")
PROMPT_KEYS = ("prompt", "messages", "input", "rendered_prompt")
DOCUMENT_TEXT_KEYS = ("text", "content", "body", "chunk", "passage", "page_content")
DOCUMENT_ID_KEYS = ("id", "doc_id", "document_id", "source_id", "uri", "source")

#: Stages whose output is a candidate for "the pipeline's final answer",
#: latest-first.
ANSWER_STAGES = (Stage.VALIDATION, Stage.POSTPROCESSING, Stage.LLM)


def first_of(payload: dict[str, Any] | None, keys: tuple[str, ...]) -> Any:
    """First present, non-empty value among ``keys``."""
    if not payload:
        return None
    for key in keys:
        if key in payload and payload[key] not in (None, "", [], {}):
            return payload[key]
    return None


def query_of(span: Span) -> str | None:
    value = first_of(span.inputs, QUERY_KEYS)
    return flatten_text(value) if value is not None else None


def documents_of(span: Span) -> list[Any]:
    """The retrieved items a span produced, as a flat list."""
    value = first_of(span.outputs, DOCUMENT_KEYS)
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def document_text(document: Any) -> str:
    if isinstance(document, dict):
        text = first_of(document, DOCUMENT_TEXT_KEYS)
        return flatten_text(text if text is not None else document)
    return flatten_text(document)


def document_id(document: Any) -> str | None:
    if isinstance(document, dict):
        value = first_of(document, DOCUMENT_ID_KEYS)
        return str(value) if value is not None else None
    return None


def answer_of(span: Span) -> str | None:
    value = first_of(span.outputs, ANSWER_KEYS)
    return flatten_text(value) if value is not None else None


def prompt_of(span: Span) -> str | None:
    """The prompt a span produced, or was given."""
    value = first_of(span.outputs, PROMPT_KEYS) or first_of(span.inputs, PROMPT_KEYS)
    return flatten_text(value) if value is not None else None


def final_answer_span(trace: Trace) -> Span | None:
    """The span whose output is the pipeline's answer to the user.

    Later stages win: if a post-processor ran after the model, what it emitted
    is what the user saw, and that is what a claim has to be checked against.
    """
    ordered = trace.ordered_spans()
    for stage in ANSWER_STAGES:
        candidates = [s for s in ordered if s.stage is stage and answer_of(s) is not None]
        if candidates:
            return candidates[-1]
    return None


def source_material(trace: Trace) -> str:
    """Everything the pipeline was legitimately allowed to base an answer on.

    Retrieved documents and tool results, concatenated. This is the corpus an
    unsupported-claim check compares the final answer against.
    """
    parts: list[str] = []
    for span in trace.ordered_spans():
        if span.stage in (Stage.RETRIEVAL, Stage.DOCUMENT_LOAD, Stage.CHUNKING):
            parts.extend(document_text(d) for d in documents_of(span))
            parts.append(flatten_text(span.outputs))
        elif span.stage is Stage.TOOL:
            parts.append(flatten_text(span.outputs))
    return "\n".join(p for p in parts if p)


def as_datetime(value: Any) -> datetime | None:
    """Parse a document date field, always returning a UTC-aware datetime.

    Normalising here rather than at each comparison site keeps the naive/aware
    mismatch that ``datetime`` comparisons raise on out of the detectors.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
