"""Phase 1 acceptance: the trace/span/event data model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from tracelens.ids import deterministic_ids, is_valid_span_id, is_valid_trace_id
from tracelens.models import (
    CaptureMode,
    ErrorInfo,
    Event,
    Severity,
    Span,
    SpanStatus,
    Stage,
    Trace,
)
from tracelens.redaction import redact

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def build_span(trace: Trace, name: str, start: float, end: float | None = 1.0, **kwargs) -> Span:
    return Span(
        trace_id=trace.trace_id,
        name=name,
        start_time=at(start),
        end_time=None if end is None else at(end),
        **kwargs,
    )


# -- ids -----------------------------------------------------------------


def test_generated_ids_are_otel_shaped():
    trace = Trace(name="run")
    span = build_span(trace, "step", 0)
    assert is_valid_trace_id(trace.trace_id)
    assert is_valid_span_id(span.span_id)


def test_ids_are_unique_across_many_spans():
    trace = Trace(name="run")
    ids = {build_span(trace, f"s{i}", 0).span_id for i in range(500)}
    assert len(ids) == 500


def test_deterministic_ids_are_reproducible():
    with deterministic_ids(1234):
        first = Trace(name="run").trace_id
    with deterministic_ids(1234):
        second = Trace(name="run").trace_id
    assert first == second

    with deterministic_ids(9999):
        assert Trace(name="run").trace_id != first


def test_deterministic_ids_restore_the_previous_generator():
    with deterministic_ids(1):
        pass
    assert Trace(name="run").trace_id != Trace(name="run").trace_id


def test_all_zero_ids_are_rejected():
    # OpenTelemetry reserves the all-zero id to mean "no id".
    with pytest.raises(ValidationError):
        Trace(trace_id="0" * 32, name="run")
    with pytest.raises(ValidationError):
        Span(trace_id="a" * 32, span_id="0" * 16, name="s")


@pytest.mark.parametrize("bad", ["", "xyz", "A" * 32, "a" * 31, "a" * 33])
def test_malformed_trace_ids_are_rejected(bad):
    with pytest.raises(ValidationError):
        Trace(trace_id=bad, name="run")


# -- single trace --------------------------------------------------------


def test_single_trace_with_one_span():
    trace = Trace(name="support-request", start_time=T0, end_time=at(2))
    span = trace.add_span(build_span(trace, "retrieval", 0, 1, stage=Stage.RETRIEVAL))

    assert trace.spans == [span]
    assert trace.duration_ms == 2000.0
    assert span.duration_ms == 1000.0
    assert span.stage is Stage.RETRIEVAL
    assert not trace.failed
    assert trace.structural_errors() == []


def test_open_span_has_no_duration():
    trace = Trace(name="run")
    span = trace.add_span(build_span(trace, "llm", 0, None))
    assert span.is_open
    assert span.duration_ms is None


def test_span_lookup_and_stage_filter():
    trace = Trace(name="run")
    a = trace.add_span(build_span(trace, "retrieval", 0, stage=Stage.RETRIEVAL))
    trace.add_span(build_span(trace, "llm", 1, 2, stage=Stage.LLM))

    assert trace.span(a.span_id) is a
    assert trace.span("f" * 16) is None
    assert [s.name for s in trace.stage_spans(Stage.LLM)] == ["llm"]


# -- nested spans --------------------------------------------------------


def test_nested_spans_report_parent_and_children():
    trace = Trace(name="rag", start_time=T0, end_time=at(10))
    root = trace.add_span(build_span(trace, "pipeline", 0, 10))
    child = trace.add_span(
        build_span(trace, "retrieval", 1, 3, parent_span_id=root.span_id, stage=Stage.RETRIEVAL)
    )
    grandchild = trace.add_span(
        build_span(trace, "vector-search", 1.5, 2.5, parent_span_id=child.span_id)
    )

    assert trace.root_spans == [root]
    assert trace.children_of(root.span_id) == [child]
    assert trace.children_of(child.span_id) == [grandchild]
    assert trace.structural_errors() == []


def test_ordered_spans_sorts_by_start_time_not_insertion():
    trace = Trace(name="rag")
    late = trace.add_span(build_span(trace, "llm", 5, 6))
    early = trace.add_span(build_span(trace, "retrieval", 1, 2))

    assert [s.name for s in trace.ordered_spans()] == ["retrieval", "llm"]
    assert trace.ordered_spans() == [early, late]


def test_ordered_spans_breaks_ties_by_insertion_order():
    trace = Trace(name="rag")
    first = trace.add_span(build_span(trace, "a", 1, 2))
    second = trace.add_span(build_span(trace, "b", 1, 2))
    assert trace.ordered_spans() == [first, second]


def test_duplicate_span_ids_are_rejected():
    trace = Trace(name="rag")
    span = trace.add_span(build_span(trace, "a", 0))
    clone = build_span(trace, "b", 1)
    clone.span_id = span.span_id

    with pytest.raises(ValueError, match="duplicate span_id"):
        trace.add_span(clone)
    # The failed append must not leave the trace corrupted.
    assert len(trace.spans) == 1


def test_span_belonging_to_another_trace_is_rejected():
    trace = Trace(name="rag")
    other = Trace(name="other")
    with pytest.raises(ValueError, match="belongs to trace"):
        trace.add_span(Span(trace_id=other.trace_id, name="stray"))


def test_self_parenting_span_is_rejected():
    with pytest.raises(ValidationError, match="its own parent"):
        Span(trace_id="a" * 32, span_id="b" * 16, parent_span_id="b" * 16, name="s")


def test_parent_cycle_is_rejected():
    trace = Trace(name="rag")
    a = build_span(trace, "a", 0)
    b = build_span(trace, "b", 1)
    a.parent_span_id = b.span_id
    b.parent_span_id = a.span_id

    with pytest.raises(ValueError, match="parent cycle"):
        Trace(name="rag", trace_id=trace.trace_id, spans=[a, b])


# -- missing parent ------------------------------------------------------


def test_missing_parent_is_reported_but_does_not_raise():
    # A child can legitimately arrive before its parent during streaming
    # ingestion, so this is a reported problem rather than a rejection.
    trace = Trace(name="rag")
    orphan = trace.add_span(build_span(trace, "retrieval", 1, 2, parent_span_id="c" * 16))

    problems = trace.structural_errors()
    assert len(problems) == 1
    assert "missing parent" in problems[0]
    assert trace.root_spans == [orphan]


def test_child_starting_before_parent_is_reported():
    trace = Trace(name="rag")
    root = trace.add_span(build_span(trace, "pipeline", 5, 10))
    trace.add_span(build_span(trace, "early", 1, 2, parent_span_id=root.span_id))

    assert any("starts before its parent" in p for p in trace.structural_errors())


def test_child_ending_after_parent_is_reported():
    trace = Trace(name="rag")
    root = trace.add_span(build_span(trace, "pipeline", 0, 5))
    trace.add_span(build_span(trace, "overrun", 1, 9, parent_span_id=root.span_id))

    assert any("ends after its parent" in p for p in trace.structural_errors())


def test_span_left_open_in_a_finished_trace_is_reported():
    trace = Trace(name="rag", start_time=T0, end_time=at(5))
    trace.add_span(build_span(trace, "leaked", 1, None))

    assert any("never ended" in p for p in trace.structural_errors())


# -- invalid timestamps --------------------------------------------------


def test_span_ending_before_it_starts_is_rejected():
    with pytest.raises(ValidationError, match="ends before it starts"):
        Span(trace_id="a" * 32, name="s", start_time=at(5), end_time=at(1))


def test_trace_ending_before_it_starts_is_rejected():
    with pytest.raises(ValidationError, match="ends before it starts"):
        Trace(name="run", start_time=at(5), end_time=at(1))


def test_zero_duration_span_is_allowed():
    span = Span(trace_id="a" * 32, name="s", start_time=T0, end_time=T0)
    assert span.duration_ms == 0.0


def test_naive_datetimes_are_read_as_utc():
    naive = datetime(2026, 1, 1, 12, 0, 0)
    span = Span(trace_id="a" * 32, name="s", start_time=naive)
    assert span.start_time == T0
    assert span.start_time.tzinfo is UTC


def test_non_utc_timestamps_are_normalised():
    plus_two = datetime(2026, 1, 1, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    span = Span(trace_id="a" * 32, name="s", start_time=plus_two)
    assert span.start_time == T0


def test_empty_names_are_rejected():
    with pytest.raises(ValidationError):
        Trace(name="   ")
    with pytest.raises(ValidationError):
        Span(trace_id="a" * 32, name="")
    with pytest.raises(ValidationError):
        Event(name=" ")


def test_unknown_fields_are_rejected():
    # extra="forbid" keeps a typo from silently becoming an attribute.
    with pytest.raises(ValidationError):
        Span(trace_id="a" * 32, name="s", statuss="ok")


# -- error states --------------------------------------------------------


def test_error_span_is_marked_failed():
    trace = Trace(name="run")
    span = trace.add_span(
        build_span(
            trace,
            "tool",
            0,
            1,
            stage=Stage.TOOL,
            status=SpanStatus.ERROR,
            error=ErrorInfo(type="TimeoutError", message="upstream timed out"),
        )
    )

    assert span.failed
    assert trace.failed
    assert str(span.error) == "TimeoutError: upstream timed out"


def test_error_attached_to_an_ok_span_is_rejected():
    with pytest.raises(ValidationError, match="error but status ok"):
        Span(
            trace_id="a" * 32,
            name="s",
            status=SpanStatus.OK,
            error=ErrorInfo(type="ValueError"),
        )


def test_error_without_a_status_still_counts_as_failed():
    span = Span(trace_id="a" * 32, name="s", error=ErrorInfo(type="ValueError"))
    assert span.status is SpanStatus.UNSET
    assert span.failed


def test_healthy_trace_is_not_failed():
    trace = Trace(name="run", status=SpanStatus.OK)
    trace.add_span(build_span(trace, "a", 0, status=SpanStatus.OK))
    assert not trace.failed


# -- events --------------------------------------------------------------


def test_record_event_appends_in_order():
    span = Span(trace_id="a" * 32, name="s")
    span.record_event("retrieved", count=3)
    span.record_event("filtered", count=1)

    assert [e.name for e in span.events] == ["retrieved", "filtered"]
    assert span.events[0].attributes == {"count": 3}


# -- serialization round trip -------------------------------------------


def test_serialization_round_trip_preserves_everything():
    trace = Trace(
        name="rag",
        project="demo",
        pipeline="support",
        start_time=T0,
        end_time=at(10),
        status=SpanStatus.ERROR,
        attributes={"version": "1.2.3"},
    )
    root = trace.add_span(build_span(trace, "pipeline", 0, 10))
    child = trace.add_span(
        build_span(
            trace,
            "retrieval",
            1,
            3,
            parent_span_id=root.span_id,
            stage=Stage.RETRIEVAL,
            status=SpanStatus.ERROR,
            error=ErrorInfo(type="LookupError", message="no documents", stacktrace="..."),
            inputs={"query": "refund policy"},
            outputs={"documents": [{"id": "doc-1"}]},
            attributes={"top_k": 5},
        )
    )
    child.record_event("cache_miss", key="refund policy")

    restored = Trace.model_validate_json(trace.model_dump_json())

    assert restored == trace
    assert restored.spans[1].error.stacktrace == "..."
    assert restored.spans[1].events[0].attributes == {"key": "refund policy"}
    assert restored.spans[1].stage is Stage.RETRIEVAL


def test_round_trip_through_plain_dict():
    trace = Trace(name="rag", start_time=T0, end_time=at(1))
    trace.add_span(build_span(trace, "a", 0, 1, stage=Stage.LLM))

    assert Trace.model_validate(trace.model_dump()) == trace


def test_json_uses_stage_and_status_string_values():
    trace = Trace(name="rag", status=SpanStatus.OK)
    trace.add_span(build_span(trace, "a", 0, 1, stage=Stage.RETRIEVAL))
    payload = trace.model_dump(mode="json")

    assert payload["status"] == "ok"
    assert payload["spans"][0]["stage"] == "retrieval"


def test_severity_weights_are_ordered():
    weights = [s.weight for s in Severity]
    assert weights == sorted(weights)


# -- redaction -----------------------------------------------------------


def test_redacted_mode_masks_secret_keys():
    payload = {"query": "hello", "api_key": "abc123", "nested": {"Authorization": "Bearer x"}}
    out = redact(payload, CaptureMode.REDACTED)

    assert out["query"] == "hello"
    assert out["api_key"] == "[REDACTED]"
    assert out["nested"]["Authorization"] == "[REDACTED]"


def test_redacted_mode_masks_secret_shaped_values_under_innocent_keys():
    out = redact({"note": "use sk-ant-abcdefghijklmnopqrstuvwx to call"}, CaptureMode.REDACTED)
    assert "sk-ant-" not in out["note"]
    assert "[REDACTED]" in out["note"]


def test_redaction_recurses_into_lists():
    out = redact({"docs": [{"token": "t"}, {"body": "ok"}]}, CaptureMode.REDACTED)
    assert out["docs"][0]["token"] == "[REDACTED]"
    assert out["docs"][1]["body"] == "ok"


def test_full_mode_changes_nothing():
    payload = {"api_key": "abc123"}
    assert redact(payload, CaptureMode.FULL) == payload


def test_metadata_mode_keeps_shape_but_drops_values():
    out = redact({"query": "refund policy", "top_k": 5}, CaptureMode.METADATA)
    assert out["query"] == "<str len=13>"
    assert out["top_k"] == 5


def test_redaction_survives_deeply_nested_payloads():
    payload: dict = {}
    cursor = payload
    for _ in range(50):
        cursor["next"] = {}
        cursor = cursor["next"]
    cursor["api_key"] = "secret"

    redact(payload, CaptureMode.REDACTED)  # must not recurse without bound


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        ("api_key", True),
        ("apiKey", True),
        ("APIKey", True),
        ("access_token", True),
        ("token", True),
        ("Authorization", True),
        ("private_key", True),
        ("card_number", True),
        ("password", True),
        # These must survive: they are the useful metadata on an LLM span.
        ("tokens", False),
        ("prompt_tokens", False),
        ("completion_tokens", False),
        ("author", False),
        ("key", False),
        ("cache_key", False),
        ("query", False),
        ("top_k", False),
    ],
)
def test_secret_key_matching_is_segment_exact(key, secret):
    from tracelens.redaction import looks_secret

    assert looks_secret(key) is secret
