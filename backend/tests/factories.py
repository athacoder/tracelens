"""Hand-built traces for forensic tests.

Every forensic test constructs its trace explicitly rather than running a
pipeline, so the input to the engine is unambiguous and the assertion is about
the engine's reasoning rather than about a fixture's behaviour. Timestamps are
fixed, so latency findings are reproducible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tracelens.models import ErrorInfo, Span, SpanStatus, Stage, Trace

#: A fixed clock. Any date in a document fixture is compared against this.
T0 = datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC)

CURRENT_POLICY = {
    "id": "policy-2026",
    "text": "Customers may return any item within 30 days of delivery for a full refund.",
    "effective_date": "2026-01-01",
    "status": "current",
}

OUTDATED_POLICY = {
    "id": "policy-2019",
    "text": "Customers may return any item within 90 days of delivery for a full refund.",
    "effective_date": "2019-01-01",
    "valid_until": "2025-12-31",
    "superseded_by": "policy-2026",
    "status": "outdated",
}

UNRELATED_DOC = {
    "id": "shipping-2026",
    "text": "Standard shipping takes three to five business days within the country.",
    "status": "current",
}


class TraceFactory:
    """Builds a trace one stage at a time on a deterministic clock."""

    def __init__(
        self,
        name: str = "rag-query",
        project: str = "demo",
        started_at: datetime = T0,
    ) -> None:
        self.origin = started_at
        self.trace = Trace(name=name, project=project, pipeline="rag", start_time=started_at)
        self._cursor = 0.0

    def add(
        self,
        name: str,
        stage: Stage,
        *,
        duration_s: float = 0.05,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
        error: ErrorInfo | None = None,
        status: SpanStatus | None = None,
        gap_s: float = 0.0,
    ) -> Span:
        start = self.origin + timedelta(seconds=self._cursor + gap_s)
        end = start + timedelta(seconds=duration_s)
        self._cursor += gap_s + duration_s
        span = Span(
            trace_id=self.trace.trace_id,
            name=name,
            stage=stage,
            start_time=start,
            end_time=end,
            inputs=inputs or {},
            outputs=outputs or {},
            attributes=attributes or {},
            error=error,
            status=status or (SpanStatus.ERROR if error else SpanStatus.OK),
        )
        return self.trace.add_span(span)

    def finish(self, status: SpanStatus | None = None) -> Trace:
        self.trace.end_time = self.origin + timedelta(seconds=self._cursor)
        self.trace.status = status or (SpanStatus.ERROR if self.trace.failed else SpanStatus.OK)
        return self.trace


QUESTION = "How many days do customers have to return an item for a refund?"


def healthy_trace() -> Trace:
    """A run where every stage did its job. The control case.

    Nothing in this trace should produce a finding. A detector that fires here
    is a false positive, which is the failure mode that makes a forensic tool
    useless in practice.
    """
    factory = TraceFactory("healthy-rag")
    factory.add(
        "preprocess",
        Stage.PREPROCESSING,
        inputs={"user_input": QUESTION},
        outputs={"query": QUESTION, "user_id": "u-42"},
    )
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION, "top_k": 3},
        outputs={"documents": [CURRENT_POLICY]},
    )
    factory.add(
        "prompt-builder",
        Stage.PROMPT_BUILD,
        inputs={"query": QUESTION, "documents": [CURRENT_POLICY]},
        outputs={"prompt": f"Context: {CURRENT_POLICY['text']}\n\nQuestion: {QUESTION}"},
    )
    factory.add(
        "llm",
        Stage.LLM,
        duration_s=0.4,
        inputs={"prompt": f"Context: {CURRENT_POLICY['text']}\n\nQuestion: {QUESTION}"},
        outputs={"answer": "Customers have 30 days to return an item for a full refund."},
        attributes={"provider": "mock", "model": "mock-1", "prompt_version": "v1"},
    )
    factory.add(
        "validator",
        Stage.VALIDATION,
        inputs={"answer": "Customers have 30 days to return an item for a full refund."},
        outputs={"answer": "Customers have 30 days to return an item for a full refund."},
    )
    return factory.finish()


def retrieval_failure_trace() -> Trace:
    """Scenario A: the retriever returns the superseded policy.

    Every downstream stage behaves correctly. The prompt faithfully carries
    what it was given, and the model faithfully answers from that prompt. The
    only thing wrong happened at retrieval, and the answer is wrong anyway.
    """
    factory = TraceFactory("retrieval-failure")
    factory.add(
        "preprocess",
        Stage.PREPROCESSING,
        inputs={"user_input": QUESTION},
        outputs={"query": QUESTION, "user_id": "u-42"},
    )
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION, "top_k": 3, "expected_document_id": "policy-2026"},
        outputs={"documents": [OUTDATED_POLICY]},
    )
    prompt = f"Context: {OUTDATED_POLICY['text']}\n\nQuestion: {QUESTION}"
    factory.add(
        "prompt-builder",
        Stage.PROMPT_BUILD,
        inputs={"query": QUESTION, "documents": [OUTDATED_POLICY]},
        outputs={"prompt": prompt},
    )
    factory.add(
        "llm",
        Stage.LLM,
        duration_s=0.4,
        inputs={"prompt": prompt},
        outputs={"answer": "Customers have 90 days to return an item for a full refund."},
        attributes={"provider": "mock", "model": "mock-1", "prompt_version": "v1"},
    )
    return factory.finish()


def model_failure_trace() -> Trace:
    """Scenario B: correct evidence, correct prompt, contradictory answer.

    The retriever found the right document and the prompt carried it. The
    model asserted a number that appears nowhere in what it was given.
    """
    factory = TraceFactory("model-failure")
    factory.add(
        "preprocess",
        Stage.PREPROCESSING,
        inputs={"user_input": QUESTION},
        outputs={"query": QUESTION, "user_id": "u-42"},
    )
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION, "top_k": 3},
        outputs={"documents": [CURRENT_POLICY]},
    )
    prompt = f"Context: {CURRENT_POLICY['text']}\n\nQuestion: {QUESTION}"
    factory.add(
        "prompt-builder",
        Stage.PROMPT_BUILD,
        inputs={"query": QUESTION, "documents": [CURRENT_POLICY]},
        outputs={"prompt": prompt},
    )
    factory.add(
        "llm",
        Stage.LLM,
        duration_s=0.4,
        inputs={"prompt": prompt},
        outputs={"answer": "Customers have 14 days to return an item for a full refund."},
        attributes={"provider": "mock", "model": "mock-1", "prompt_version": "v1"},
    )
    return factory.finish()


def postprocessing_failure_trace() -> Trace:
    """Scenario C: the model was right and the post-processor broke it."""
    factory = TraceFactory("postprocessing-failure")
    factory.add(
        "preprocess",
        Stage.PREPROCESSING,
        inputs={"user_input": QUESTION},
        outputs={"query": QUESTION, "user_id": "u-42"},
    )
    factory.add(
        "retriever",
        Stage.RETRIEVAL,
        inputs={"query": QUESTION, "top_k": 3},
        outputs={"documents": [CURRENT_POLICY]},
    )
    prompt = f"Context: {CURRENT_POLICY['text']}\n\nQuestion: {QUESTION}"
    factory.add(
        "prompt-builder",
        Stage.PROMPT_BUILD,
        inputs={"query": QUESTION, "documents": [CURRENT_POLICY]},
        outputs={"prompt": prompt},
    )
    correct = "Customers have 30 days to return an item for a full refund."
    factory.add(
        "llm",
        Stage.LLM,
        duration_s=0.4,
        inputs={"prompt": prompt},
        outputs={"answer": correct},
        attributes={"provider": "mock", "model": "mock-1", "prompt_version": "v1"},
    )
    factory.add(
        "formatter",
        Stage.POSTPROCESSING,
        inputs={"answer": correct},
        outputs={"answer": "Customers have 3 days to return an item for a full refund."},
    )
    return factory.finish()


def tool_timeout_trace() -> Trace:
    """A tool call that raised, and a downstream stage that suffered for it."""
    factory = TraceFactory("tool-timeout")
    factory.add(
        "preprocess",
        Stage.PREPROCESSING,
        inputs={"user_input": "What is my order status?"},
        outputs={"query": "What is my order status?", "user_id": "u-42"},
    )
    factory.add(
        "order-api",
        Stage.TOOL,
        duration_s=2.0,
        inputs={"order_id": "ord-9"},
        outputs={},
        error=ErrorInfo(type="TimeoutError", message="order-api did not respond in 2000ms"),
    )
    factory.add(
        "llm",
        Stage.LLM,
        duration_s=0.3,
        inputs={"prompt": "Question: What is my order status?"},
        outputs={"answer": "Your order shipped on 2026-05-30."},
    )
    return factory.finish()
