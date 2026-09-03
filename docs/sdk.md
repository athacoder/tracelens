# SDK reference

`tracelens` instruments a pipeline and ships traces. It depends on Pydantic and
httpx, and on nothing in the backend.

## Install

```bash
pip install -e .            # from this repository
```

## The whole surface

```python
from tracelens import configure, trace, record_event, Stage, HttpExporter

configure(project="support", pipeline="rag", exporter=HttpExporter("http://localhost:8000"))

with trace("customer-support"):
    with trace("retriever", stage=Stage.RETRIEVAL):
        record_event("cache_miss")
```

`trace(name)` opens a **trace** when nothing is running and a **child span**
when something is. That is what lets one name serve both positions without the
caller tracking nesting depth.

It also works as a decorator. A fresh scope is opened per call, not per
decoration:

```python
@trace("retriever", stage=Stage.RETRIEVAL)
def retrieve(query: str, top_k: int = 3) -> list[dict]: ...
```

Called outside a trace, the decorated function opens its own — so a single
instrumented function is still recorded.

Both sync and async functions are supported; a coroutine gets a real span, and
`asyncio.gather` fan-out lands under one trace because propagation is
`contextvars`-based.

## Instrumentation never breaks your pipeline

A dead exporter, an unserialisable payload, or a span closed out of order is
logged, not raised. A tracing library that takes production down has failed at
its only job.

```python
Tracer(strict=True)  # raise instead — used by this project's own tests
```

The tests run `strict=True` so no test can pass because an instrumentation
mistake was quietly logged.

## Explicit tracers

The module-level functions use a default tracer. For explicit control, or for
more than one configuration in a process:

```python
from tracelens import Tracer, MemoryExporter
from tracelens.models import CaptureMode

tracer = Tracer(
    project="support",
    pipeline="rag",
    exporter=MemoryExporter(),
    capture=CaptureMode.FULL,
    strict=False,
)

with tracer.trace("support-request"):
    docs = tracer.run("retriever", retrieve, query, stage=Stage.RETRIEVAL)
    answer = tracer.run("llm", generate, docs, stage=Stage.LLM)
```

`run()` and `arun()` wrap a call in a span, capturing arguments **by parameter
name** and the return value. Named arguments are what later let the invariant
engine follow a value across a stage boundary — a positional tuple would not.

### Primitives

For code that cannot use a context manager:

```python
trace = tracer.start_trace("manual", source="worker")
span = tracer.start_span("retriever", stage=Stage.RETRIEVAL, inputs={"query": q})
tracer.record_event("hit", count=2)
tracer.set_span_status(SpanStatus.OK)
tracer.attach_error(exc)
tracer.end_span(span, outputs={"documents": docs})
tracer.end_trace(trace)
```

Prefer the context managers: they cannot leak on an early return.

## Payload conventions

TraceLens reads payloads written by code it does not control, so each accessor
tries the names actually used in practice and abstains when it finds none. A
detector that cannot locate its subject reports nothing rather than guessing.

Naming your fields from this table is what unlocks the stronger detectors.

| Concept | Keys tried, in order | Read from |
|---|---|---|
| query | `query`, `question`, `input`, `user_input`, `prompt`, `text` | inputs |
| documents | `documents`, `docs`, `results`, `chunks`, `context`, `passages` | outputs |
| document text | `text`, `content`, `body`, `chunk`, `passage`, `page_content` | each document |
| document id | `id`, `doc_id`, `document_id`, `source_id`, `uri`, `source` | each document |
| prompt | `prompt`, `messages`, `input`, `rendered_prompt` | outputs, then inputs |
| answer | `answer`, `text`, `completion`, `output`, `response`, `result`, `content` | outputs |

### Document metadata that unlocks stale detection

A stale document is the failure that looks most like success: real, on-topic,
well-formed, and wrong. Nothing except its own metadata reveals it.

```python
{
    "id": "refund-2026",
    "text": "Customers may return any item within 30 days ...",
    "status": "current",  # or outdated / archived / superseded
    "effective_date": "2026-01-01",
    "valid_until": "2025-12-31",  # compared against the trace start time
    "superseded_by": "refund-2026",
}
```

### Declaring expectations

```python
# Retrieval knows which document should have come back:
tracer.set_inputs(expected_document_id="refund-2026")

# A stage can declare its own output contract:
tracer.set_attributes(expects_outputs=["documents", "scores"])

# Or which fields must survive it:
tracer.set_attributes(required_fields=["query", "user_id"])
```

Each turns a heuristic into a rule, and raises the confidence of any finding
that follows from it.

### Measurement fields

Fields whose names read as measurements — anything containing `count`,
`characters`, `chars`, `length`, `len`, `size`, `bytes`, `tokens`, `ms`,
`duration`, `latency`, `index`, `offset`, `seq`, `sequence` — are treated as a
stage's own instrumentation rather than content it carried.

This matters for the "did this stage alter a value in transit?" check. Without
it, a stage reporting `chunk_count` alongside its output looks like it invented
a number. Emit counts freely; they will not be mistaken for payload.

## Stages

`preprocessing`, `document_load`, `chunking`, `retrieval`, `prompt_build`,
`llm`, `tool`, `postprocessing`, `validation`, `other`.

The stage is what selects which detectors apply, so it is worth setting
accurately. `other` is safe but disables stage-specific checks.

## Recording model and prompt versions

```python
with tracer.span(
    "llm", stage=Stage.LLM, provider="anthropic", model="claude-opus-5", prompt_version="v3"
):
    ...
```

Never rely on a human remembering which prompt and model produced a trace; the
comparison you will want to make is months later.

## Capture policy and redaction

```python
Tracer(capture=CaptureMode.FULL)  # verbatim
Tracer(capture=CaptureMode.REDACTED)  # default: mask secret-looking keys and values
Tracer(capture=CaptureMode.METADATA)  # types and sizes only, never values
```

Structure is preserved in every mode, so the engine still sees which keys were
present even when values are hidden.

Redaction matches on **whole key segments**, not substrings. `access_token` is
masked; `prompt_tokens`, `completion_tokens`, and `tokens` are not — masking
those would destroy the most useful metadata on an LLM span. It also masks
secret-shaped values (OpenAI, Anthropic, GitHub, AWS, JWT, bearer) under
innocent-looking keys.

It is a sensible default, not a DLP solution.

## Exporters

```python
from tracelens import MemoryExporter, FileExporter, HttpExporter

MemoryExporter()  # default; keeps traces in a list
FileExporter("traces.jsonl")  # append JSONL, replayable
HttpExporter(base_url="http://localhost:8000")  # POST /api/v1/traces
```

The default is in-process, so importing `tracelens` never makes a network call
the caller did not ask for. Any object with `export(trace) -> None` works.

```python
from tracelens import configure_from_env  # reads TRACELENS_API_URL, _CAPTURE_MODE, ...
```

## Reading traces back

```python
from tracelens import TraceLensClient

client = TraceLensClient("http://localhost:8000")
report = client.get_root_cause_report(trace_id)
```

The API key, when set, is sent as a bearer token and never written into a
trace, a log line, or an error message.

## Deterministic ids

```python
from tracelens import deterministic_ids

with deterministic_ids(seed=42):
    ...  # trace and span ids are reproducible
```

For tests and seeded benchmark runs. Not for production: the ids are
predictable by construction.
