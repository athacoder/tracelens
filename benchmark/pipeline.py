"""A real RAG pipeline, instrumented with the TraceLens SDK, with a switch at
every stage that can break it.

Two rules keep this honest as a benchmark.

First, the pipeline is instrumented the way a user would instrument theirs:
through the public SDK, with no privileged channel to the forensic engine. The
engine sees exactly what it would see in production.

Second, the injections break the pipeline, they do not annotate it. Nothing in
the trace says "this run is scenario outdated_document". The retriever simply
returns a different document, and the engine has to work out the rest.

The model is a deterministic extractive stand-in, not a language model. A real
model would make the benchmark unreproducible and would require a paid API on
every run, which section 16 forbids for the default path. What is being
measured here is the forensic engine, not a model's fluency.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from tracelens import MemoryExporter, Stage, Tracer
from tracelens.models import CaptureMode, Trace

from .corpus import (
    BENCHMARK_NOW,
    DOCUMENTS,
    DOCUMENTS_BY_ID,
    Document,
    Question,
    wrong_topic_document,
)

TOP_K = 3
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

#: Scenarios in which the retriever returns the superseded document. Named as
#: a set because the harder scenarios combine this fault with another one.
STALE_RETRIEVAL_SCENARIOS = frozenset(
    {
        "outdated_document",
        "stale_retrieval_with_slow_model",
        "compound_retrieval_and_postprocessing",
    }
)

#: Scenarios in which the post-processor rewrites a value.
CORRUPT_POSTPROCESSING_SCENARIOS = frozenset(
    {"postprocessing_corruption", "compound_retrieval_and_postprocessing"}
)

#: Long enough to clear the detector's absolute floor, short enough that the
#: whole benchmark still runs in seconds. Real sleep rather than a doctored
#: timestamp, so the latency the engine sees is latency that happened.
SLOW_STAGE_SECONDS = 0.55


@dataclass
class PipelineResult:
    trace: Trace
    answer: str
    question: Question
    scenario: str


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def _score(query: str, text: str) -> float:
    """Lexical overlap. The retriever is a bag-of-words matcher on purpose.

    A better retriever would make the healthy case cleaner but would not change
    what the benchmark measures, and a transparent one keeps every retrieval
    decision auditable.
    """
    query_words = _words(query)
    if not query_words:
        return 0.0
    return len(query_words & _words(text)) / len(query_words)


class MockLLM:
    """Extractive, deterministic, and honest about being a stand-in.

    Answers by returning the sentence of its context that best matches the
    question. With no context it says so rather than inventing an answer,
    which is what makes the missing-context scenario show up at retrieval
    instead of masquerading as a hallucination.
    """

    provider = "mock"
    model = "extractive-1"
    prompt_version = "v1"

    def generate(self, prompt: str, question: str) -> str:
        context = prompt.split("Question:")[0].removeprefix("Context:").strip()
        candidates = _sentences(context)
        if not candidates:
            return "I could not find that information in the provided context."
        best = max(candidates, key=lambda sentence: _score(question, sentence))
        return best if best.endswith(".") else best + "."


def run_pipeline(
    question: Question,
    scenario: str = "healthy",
    tracer: Tracer | None = None,
    seed: int = 0,
) -> PipelineResult:
    """Run one question through the pipeline under one scenario.

    The pipeline itself contains no randomness, so a trace's content is fully
    determined by ``question`` and ``scenario``. ``seed`` is applied by the
    caller through ``tracelens.deterministic_ids``, which makes two runs of the
    same command byte-identical rather than merely equivalent.
    """
    del seed  # see above: applied by the caller, not here
    tracer = tracer or Tracer(
        project="benchmark",
        pipeline="rag",
        exporter=MemoryExporter(),
        capture=CaptureMode.FULL,
    )
    llm = MockLLM()

    with tracer.trace(
        f"benchmark:{question.id}",
        scenario=scenario,
        benchmark=True,
        synthetic=True,
    ) as trace:
        query = _preprocess(tracer, question)
        documents = _load(tracer, scenario)
        chunks = _chunk(tracer, documents, scenario)
        retrieved = _retrieve(tracer, query, chunks, question, scenario)
        extracted = _extract(tracer, retrieved, scenario)
        prompt = _build_prompt(tracer, query, retrieved, scenario)
        raw_answer = _generate(tracer, llm, prompt, query, question, scenario)
        answer = _postprocess(tracer, raw_answer, scenario)
        _validate(tracer, answer, extracted)

    return PipelineResult(trace=trace, answer=answer, question=question, scenario=scenario)


# -- stages ---------------------------------------------------------------


def _preprocess(tracer: Tracer, question: Question) -> str:
    with tracer.span("preprocess", stage=Stage.PREPROCESSING, inputs={"user_input": question.text}):
        query = " ".join(question.text.split())
        tracer.set_outputs(query=query, user_id="user-benchmark", question_id=question.id)
        return query


def _load(tracer: Tracer, scenario: str) -> list[dict]:
    with tracer.span("document-loader", stage=Stage.DOCUMENT_LOAD, inputs={"source": "corpus"}):
        if scenario == "slow_but_correct":
            time.sleep(SLOW_STAGE_SECONDS)
        documents = [document.to_payload() for document in DOCUMENTS]
        tracer.set_outputs(documents=documents, document_count=len(documents))
        return documents


def _chunk(tracer: Tracer, documents: list[dict], scenario: str) -> list[dict]:
    with tracer.span("chunker", stage=Stage.CHUNKING, inputs={"documents": documents}):
        chunks = []
        for document in documents:
            text = document["text"]
            if scenario == "context_corruption" and document["status"] == "current":
                # Alter a value while splitting. The chunk stays well-formed
                # and on-topic; only its content is now wrong.
                text = _corrupt_numbers(text)
            chunks.append({**document, "text": text})
        tracer.set_outputs(chunks=chunks, chunk_count=len(chunks))
        return chunks


def _corrupt_numbers(text: str) -> str:
    """Replace the first number with a different one."""
    return re.sub(r"\b(\d+)\b", lambda m: str(int(m.group(1)) + 47), text, count=1)


def _retrieve(
    tracer: Tracer,
    query: str,
    chunks: list[dict],
    question: Question,
    scenario: str,
) -> list[dict]:
    with tracer.span(
        "retriever",
        stage=Stage.RETRIEVAL,
        inputs={
            "query": query,
            "top_k": TOP_K,
            "expected_document_id": question.expected_document_id,
        },
    ):
        by_id = {chunk["id"]: chunk for chunk in chunks}

        if scenario == "missing_context":
            tracer.set_outputs(documents=[], retrieved_count=0)
            return []

        if scenario == "wrong_document":
            documents = [_chunk_for(by_id, wrong_topic_document(question).id)]
        elif scenario in STALE_RETRIEVAL_SCENARIOS:
            stale_id = question.stale_document_id
            documents = [
                _chunk_for(by_id, stale_id)
                if stale_id
                else _chunk_for(by_id, wrong_topic_document(question).id)
            ]
        else:
            ranked = sorted(
                (c for c in chunks if c["status"] == "current"),
                key=lambda chunk: _score(query, chunk["text"]),
                reverse=True,
            )
            documents = ranked[:1]

        if scenario == "schema_violation":
            # Emit the right content in the wrong shape.
            tracer.set_outputs(documents=documents[0]["text"], retrieved_count=len(documents))
            return documents

        tracer.set_outputs(documents=documents, retrieved_count=len(documents))
        return documents


def _chunk_for(by_id: dict[str, dict], document_id: str) -> dict:
    return by_id[document_id] if document_id in by_id else next(iter(by_id.values()))


def _extract(tracer: Tracer, retrieved: list[dict], scenario: str) -> dict:
    """A structured-extraction tool call over the retrieved document.

    Present in every run, so a false positive on this stage would show up in
    the healthy control rather than only in the tool scenarios.
    """
    source = retrieved[0]["text"] if retrieved else ""
    try:
        with tracer.span(
            "policy-lookup",
            stage=Stage.TOOL,
            inputs={"document_text": source, "field": "primary_value"},
        ):
            if scenario == "tool_timeout":
                raise TimeoutError("policy-lookup did not respond within 2000ms")

            numbers = re.findall(r"\b\d+\b", source)
            value = numbers[0] if numbers else None

            if scenario == "wrong_tool_response" and value is not None:
                # Return a value the document does not contain.
                value = str(int(value) + 63)

            result = {"primary_value": value, "source_characters": len(source)}
            tracer.set_outputs(result=result)
            return result
    except TimeoutError:
        # Degrade rather than abort, which is what production code does when a
        # dependency times out. It is also what makes the downstream
        # consequence observable: the pipeline continues without the value.
        return {}


def _build_prompt(tracer: Tracer, query: str, retrieved: list[dict], scenario: str) -> str:
    with tracer.span(
        "prompt-builder",
        stage=Stage.PROMPT_BUILD,
        inputs={"query": query, "documents": retrieved},
    ):
        if scenario == "prompt_corruption":
            prompt = f"Question: {query}"  # context silently dropped
        else:
            context = "\n".join(document["text"] for document in retrieved)
            prompt = f"Context: {context}\n\nQuestion: {query}"
        tracer.set_outputs(prompt=prompt, prompt_characters=len(prompt))
        return prompt


def _generate(
    tracer: Tracer,
    llm: MockLLM,
    prompt: str,
    query: str,
    question: Question,
    scenario: str,
) -> str:
    with tracer.span(
        "llm",
        stage=Stage.LLM,
        inputs={"prompt": prompt},
        # Section 30: record what produced the output, so a later comparison
        # does not depend on anyone remembering.
        provider=llm.provider,
        model=llm.model,
        prompt_version=llm.prompt_version,
        benchmark_time=BENCHMARK_NOW,
    ):
        if scenario == "stale_retrieval_with_slow_model":
            time.sleep(SLOW_STAGE_SECONDS)
        answer = (
            question.hallucinated_answer
            if scenario == "unsupported_claim" and question.hallucinated_answer
            else llm.generate(prompt, query)
        )
        tracer.set_outputs(answer=answer, prompt_tokens=len(prompt.split()))
        return answer


def _postprocess(tracer: Tracer, answer: str, scenario: str) -> str:
    with tracer.span("formatter", stage=Stage.POSTPROCESSING, inputs={"answer": answer}):
        formatted = answer.strip()
        if scenario in CORRUPT_POSTPROCESSING_SCENARIOS:
            formatted = _corrupt_numbers(formatted)
        tracer.set_outputs(answer=formatted)
        return formatted


def _validate(tracer: Tracer, answer: str, extracted: dict) -> None:
    with tracer.span(
        "validator",
        stage=Stage.VALIDATION,
        inputs={"answer": answer, "extracted": extracted},
    ):
        tracer.set_outputs(answer=answer, non_empty=bool(answer.strip()))


def expected_document(question: Question) -> Document:
    return DOCUMENTS_BY_ID[question.expected_document_id]
