"""Phase 2 acceptance: the Python instrumentation SDK."""

from __future__ import annotations

import asyncio
import threading

import pytest
import tracelens
from tracelens import CaptureMode, MemoryExporter, SpanStatus, Stage, Tracer
from tracelens.tracing import context

# -- context manager behaviour ------------------------------------------


def test_trace_context_manager_records_and_exports(tracer, exporter):
    with tracer.trace("support-request") as trace:
        assert context.current_trace() is trace

    assert len(exporter) == 1
    assert exporter.last is trace
    assert trace.status is SpanStatus.OK
    assert trace.end_time is not None
    assert trace.project == "test"
    assert trace.pipeline == "rag"


def test_trace_context_is_cleared_on_exit(tracer):
    with tracer.trace("run"):
        pass
    assert context.current_trace() is None
    assert context.current_span() is None


def test_span_context_manager_attaches_to_the_trace(tracer, exporter):
    with tracer.trace("run"), tracer.span("retriever", stage=Stage.RETRIEVAL) as span:
        assert context.current_span() is span

    trace = exporter.last
    assert [s.name for s in trace.spans] == ["retriever"]
    assert trace.spans[0].stage is Stage.RETRIEVAL
    assert trace.spans[0].duration_ms is not None
    assert context.current_span() is None


def test_module_level_trace_opens_a_trace_then_spans(default_tracer):
    # This is the form the spec advertises: one name for both.
    with tracelens.trace("customer-support"):
        with tracelens.trace("retriever", stage=Stage.RETRIEVAL):
            pass

    trace = default_tracer.last
    assert trace.name == "customer-support"
    assert [s.name for s in trace.spans] == ["retriever"]


def test_spans_outside_a_trace_are_ignored_not_fatal():
    # Non-strict is the production default: a missing trace must not take the
    # caller's pipeline down.
    tracer = Tracer(exporter=MemoryExporter(), strict=False)
    with tracer.span("orphan") as span:
        assert span is None


def test_strict_mode_surfaces_instrumentation_mistakes(tracer):
    with pytest.raises(RuntimeError, match="no active trace"), tracer.span("orphan"):
        pass


# -- nested spans --------------------------------------------------------


def test_nested_spans_get_parent_links(tracer, exporter):
    with tracer.trace("rag"), tracer.span("retrieval", stage=Stage.RETRIEVAL) as outer:
        with tracer.span("vector-search") as inner:
            pass

    trace = exporter.last
    assert outer.parent_span_id is None
    assert inner.parent_span_id == outer.span_id
    assert trace.children_of(outer.span_id) == [inner]
    assert trace.structural_errors() == []


def test_sibling_spans_share_a_parent(tracer, exporter):
    with tracer.trace("rag"), tracer.span("retrieval") as parent:
        with tracer.span("embed"):
            pass
        with tracer.span("search"):
            pass

    trace = exporter.last
    assert [s.name for s in trace.children_of(parent.span_id)] == ["embed", "search"]


def test_span_stack_unwinds_to_the_right_depth(tracer):
    with tracer.trace("rag"):
        with tracer.span("a"):
            with tracer.span("b"):
                assert len(context.span_stack()) == 2
            assert len(context.span_stack()) == 1
        assert len(context.span_stack()) == 0


def test_nested_traces_restore_the_outer_trace(tracer, exporter):
    with tracer.trace("outer") as outer, tracer.span("step"):
        with tracer.trace("inner") as inner:
            assert context.current_trace() is inner
        assert context.current_trace() is outer
        assert context.current_span().name == "step"

    assert [t.name for t in exporter.traces] == ["inner", "outer"]
    assert len(outer.spans) == 1


# -- exception capture ---------------------------------------------------


def test_exception_is_recorded_on_the_span_and_re_raised(tracer, exporter):
    with pytest.raises(ValueError, match="boom"), tracer.trace("rag"):
        with tracer.span("llm", stage=Stage.LLM):
            raise ValueError("boom")

    span = exporter.last.spans[0]
    assert span.status is SpanStatus.ERROR
    assert span.error.type == "ValueError"
    assert span.error.message == "boom"
    assert "ValueError" in span.error.stacktrace
    assert span.end_time is not None


def test_a_failed_span_fails_the_whole_trace(tracer, exporter):
    with pytest.raises(ValueError), tracer.trace("rag"), tracer.span("llm"):
        raise ValueError("boom")

    assert exporter.last.status is SpanStatus.ERROR


def test_the_trace_is_still_exported_when_the_body_raises(tracer, exporter):
    with pytest.raises(RuntimeError), tracer.trace("rag"):
        raise RuntimeError("early exit")

    assert len(exporter) == 1
    assert exporter.last.status is SpanStatus.ERROR
    assert "RuntimeError: early exit" in exporter.last.attributes["error"]


def test_context_is_restored_after_an_exception(tracer):
    with pytest.raises(ValueError), tracer.trace("rag"), tracer.span("a"):
        raise ValueError("boom")

    assert context.current_trace() is None
    assert context.current_span() is None


def test_an_inner_failure_does_not_mark_an_unrelated_sibling(tracer, exporter):
    with tracer.trace("rag"):
        with tracer.span("ok-step"):
            pass
        with pytest.raises(ValueError), tracer.span("bad-step"):
            raise ValueError("boom")

    by_name = {s.name: s for s in exporter.last.spans}
    assert by_name["ok-step"].status is SpanStatus.OK
    assert by_name["bad-step"].status is SpanStatus.ERROR


# -- decorator behaviour -------------------------------------------------


def test_decorator_creates_a_span_per_call(default_tracer):
    @tracelens.trace("retriever", stage=Stage.RETRIEVAL)
    def retrieve(query):
        return ["doc-1"]

    with tracelens.trace("run"):
        assert retrieve("refund policy") == ["doc-1"]
        assert retrieve("shipping") == ["doc-1"]

    trace = default_tracer.last
    assert [s.name for s in trace.spans] == ["retriever", "retriever"]
    assert trace.spans[0].span_id != trace.spans[1].span_id


def test_decorator_captures_named_arguments_and_result(default_tracer):
    @tracelens.trace("retriever", stage=Stage.RETRIEVAL)
    def retrieve(query, top_k=3):
        return ["doc-1"]

    with tracelens.trace("run"):
        retrieve("refund policy")

    span = default_tracer.last.spans[0]
    assert span.inputs == {"query": "refund policy", "top_k": 3}
    assert span.outputs == {"result": ["doc-1"]}


def test_decorator_preserves_function_identity(default_tracer):
    @tracelens.trace("retriever")
    def retrieve(query):
        """Find documents."""
        return []

    assert retrieve.__name__ == "retrieve"
    assert retrieve.__doc__ == "Find documents."


def test_decorator_defaults_its_name_to_the_function(default_tracer):
    @tracelens.trace()
    def retrieve(query):
        return []

    with tracelens.trace("run"):
        retrieve("q")

    assert default_tracer.last.spans[0].name.endswith("retrieve")


def test_decorator_opens_its_own_trace_when_called_standalone(default_tracer):
    @tracelens.trace("retriever")
    def retrieve(query):
        return []

    retrieve("q")

    assert default_tracer.last.name == "retriever"


def test_decorator_records_exceptions(default_tracer):
    @tracelens.trace("llm", stage=Stage.LLM)
    def generate():
        raise TimeoutError("model timed out")

    with pytest.raises(TimeoutError), tracelens.trace("run"):
        generate()

    span = default_tracer.last.spans[0]
    assert span.error.type == "TimeoutError"
    assert span.status is SpanStatus.ERROR


def test_tracer_traced_decorator(tracer, exporter):
    @tracer.traced("retriever", stage=Stage.RETRIEVAL)
    def retrieve(query):
        return ["doc-1"]

    with tracer.trace("run"):
        retrieve("q")

    assert exporter.last.spans[0].outputs == {"result": ["doc-1"]}


# -- async ---------------------------------------------------------------


async def test_async_decorator_creates_a_span(default_tracer):
    @tracelens.trace("llm", stage=Stage.LLM)
    async def generate(prompt):
        await asyncio.sleep(0)
        return "answer"

    with tracelens.trace("run"):
        assert await generate("hi") == "answer"

    span = default_tracer.last.spans[0]
    assert span.stage is Stage.LLM
    assert span.outputs == {"result": "answer"}


async def test_async_decorator_records_exceptions(default_tracer):
    @tracelens.trace("llm")
    async def generate():
        raise RuntimeError("stream closed")

    with pytest.raises(RuntimeError), tracelens.trace("run"):
        await generate()

    assert default_tracer.last.spans[0].error.type == "RuntimeError"


async def test_concurrent_tasks_attach_to_the_same_trace(tracer, exporter):
    async def step(name):
        with tracer.span(name):
            await asyncio.sleep(0)

    with tracer.trace("fan-out"):
        await asyncio.gather(step("a"), step("b"), step("c"))

    assert sorted(s.name for s in exporter.last.spans) == ["a", "b", "c"]


def test_threads_do_not_share_a_trace(tracer):
    seen: list[object] = []

    def worker():
        seen.append(context.current_trace())

    with tracer.trace("main"):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

    # contextvars are not inherited by threading.Thread, so the worker must
    # not see the parent's trace.
    assert seen == [None]


# -- run() ---------------------------------------------------------------


def test_run_wraps_a_callable_in_a_span(tracer, exporter):
    def retrieve(query):
        return ["doc-1"]

    with tracer.trace("support-request"):
        docs = tracer.run("retriever", retrieve, "refund policy", stage=Stage.RETRIEVAL)

    assert docs == ["doc-1"]
    span = exporter.last.spans[0]
    assert span.name == "retriever"
    assert span.inputs == {"query": "refund policy"}
    assert span.outputs == {"result": ["doc-1"]}


async def test_arun_wraps_a_coroutine(tracer, exporter):
    async def generate(prompt):
        return "answer"

    with tracer.trace("run"):
        assert await tracer.arun("llm", generate, "hi", stage=Stage.LLM) == "answer"

    assert exporter.last.spans[0].outputs == {"result": "answer"}


def test_run_propagates_exceptions(tracer, exporter):
    def boom():
        raise KeyError("missing")

    with tracer.trace("run"), pytest.raises(KeyError):
        tracer.run("step", boom)

    assert exporter.last.spans[0].error.type == "KeyError"


# -- metadata propagation ------------------------------------------------


def test_trace_and_span_attributes_are_recorded(tracer, exporter):
    with tracer.trace("run", version="1.2.3", tenant="acme"):
        with tracer.span("llm", stage=Stage.LLM, model="claude-opus-5", prompt_version="v3"):
            pass

    trace = exporter.last
    assert trace.attributes == {"version": "1.2.3", "tenant": "acme"}
    assert trace.spans[0].attributes == {"model": "claude-opus-5", "prompt_version": "v3"}


def test_inputs_and_outputs_can_be_set_inside_a_span(tracer, exporter):
    with tracer.trace("run"), tracer.span("retriever"):
        tracer.set_inputs(query="refund policy", top_k=5)
        tracer.set_outputs(documents=["doc-1"])
        tracer.set_attributes(index="policies-v2")

    span = exporter.last.spans[0]
    assert span.inputs == {"query": "refund policy", "top_k": 5}
    assert span.outputs == {"documents": ["doc-1"]}
    assert span.attributes == {"index": "policies-v2"}


def test_events_are_recorded_on_the_innermost_span(tracer, exporter):
    with tracer.trace("run"), tracer.span("outer"), tracer.span("inner"):
        tracer.record_event("cache_miss", key="refund policy")

    by_name = {s.name: s for s in exporter.last.spans}
    assert by_name["outer"].events == []
    assert by_name["inner"].events[0].name == "cache_miss"


def test_token_counts_survive_redaction(tracer, exporter):
    # Regression: substring matching on "token" masked prompt_tokens, which is
    # the single most useful attribute on an LLM span.
    with tracer.trace("run"), tracer.span("llm", stage=Stage.LLM):
        tracer.set_outputs(prompt_tokens=812, completion_tokens=64, tokens=876)
        tracer.set_inputs(access_token="super-secret-value")

    span = exporter.last.spans[0]
    assert span.outputs == {"prompt_tokens": 812, "completion_tokens": 64, "tokens": 876}
    assert span.inputs == {"access_token": "[REDACTED]"}


def test_capture_mode_is_applied_at_the_boundary(exporter):
    tracer = Tracer(exporter=exporter, capture=CaptureMode.METADATA, strict=True)
    with tracer.trace("run"), tracer.span("retriever"):
        tracer.set_inputs(query="refund policy")

    assert exporter.last.spans[0].inputs == {"query": "<str len=13>"}


# -- primitives ----------------------------------------------------------


def test_primitive_start_end_functions(tracer, exporter):
    trace = tracer.start_trace("manual", source="primitive-api")
    span = tracer.start_span("retriever", stage=Stage.RETRIEVAL, inputs={"query": "q"})
    tracer.record_event("hit", count=2)
    tracer.set_span_status(SpanStatus.OK)
    tracer.end_span(span, outputs={"documents": ["doc-1"]})
    tracer.end_trace(trace)

    assert exporter.last is trace
    assert span.status is SpanStatus.OK
    assert span.outputs == {"documents": ["doc-1"]}
    assert span.events[0].name == "hit"
    assert context.current_trace() is None


def test_attach_error_marks_the_span_without_raising(tracer, exporter):
    with tracer.trace("run"), tracer.span("tool", stage=Stage.TOOL) as span:
        tracer.attach_error(TimeoutError("upstream timed out"), span)

    assert span.status is SpanStatus.ERROR
    assert span.error.type == "TimeoutError"
    assert exporter.last.status is SpanStatus.ERROR


# -- exporter behaviour --------------------------------------------------


def test_memory_exporter_collects_every_trace(tracer, exporter):
    for i in range(3):
        with tracer.trace(f"run-{i}"):
            pass

    assert len(exporter) == 3
    assert [t.name for t in exporter.traces] == ["run-0", "run-1", "run-2"]
    exporter.clear()
    assert exporter.last is None


def test_file_exporter_round_trips_through_jsonl(tmp_path):
    from tracelens import FileExporter

    exporter = FileExporter(tmp_path / "traces.jsonl")
    tracer = Tracer(exporter=exporter, strict=True)

    with tracer.trace("run-a"), tracer.span("retriever", stage=Stage.RETRIEVAL):
        pass
    with tracer.trace("run-b"):
        pass

    restored = exporter.read_all()
    assert [t.name for t in restored] == ["run-a", "run-b"]
    assert restored[0].spans[0].stage is Stage.RETRIEVAL


def test_a_broken_exporter_does_not_break_the_pipeline():
    class BrokenExporter:
        def export(self, trace):
            raise ConnectionError("backend unreachable")

    tracer = Tracer(exporter=BrokenExporter(), strict=False)
    result = []
    with tracer.trace("run"):
        result.append("pipeline still ran")

    assert result == ["pipeline still ran"]


def test_a_broken_exporter_is_surfaced_in_strict_mode():
    class BrokenExporter:
        def export(self, trace):
            raise ConnectionError("backend unreachable")

    tracer = Tracer(exporter=BrokenExporter(), strict=True)
    with pytest.raises(ConnectionError), tracer.trace("run"):
        pass


def test_http_exporter_posts_the_trace():
    import httpx

    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["body"] = request.content.decode()
        sent["auth"] = request.headers.get("authorization")
        return httpx.Response(202, json={"trace_id": "ok"})

    transport = httpx.MockTransport(handler)
    from tracelens import HttpExporter, TraceLensClient

    client = TraceLensClient(
        base_url="http://backend:8000",
        api_key="test-key",
        client=httpx.Client(transport=transport),
    )
    tracer = Tracer(exporter=HttpExporter(client=client), strict=True)

    with tracer.trace("run"), tracer.span("retriever", stage=Stage.RETRIEVAL):
        pass

    assert sent["url"] == "http://backend:8000/api/v1/traces"
    assert sent["auth"] == "Bearer test-key"
    assert '"name":"retriever"' in sent["body"]


def test_default_tracer_keeps_traces_in_memory():
    # Importing tracelens must never make a network call the caller did not ask
    # for, so the default exporter is in-process.
    assert isinstance(Tracer().exporter, MemoryExporter)
