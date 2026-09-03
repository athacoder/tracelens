"""Which stage fed which.

Distinguishing a root cause from its consequences requires knowing what
depends on what. A retrieval failure and the bad answer that follows it are
both real findings; only one of them is worth fixing. Without a dependency
view the engine can only report "several things are wrong", which is the
output every existing observability tool already produces.

TraceLens infers dependencies three ways, strongest first:

1. **Nesting.** A child span ran inside its parent, so it depends on it.
2. **Data flow.** A value that left one span's outputs appears in another's
   inputs. This is the strongest signal and it survives reordering.
3. **Sequence.** In a linear pipeline, each step consumes the previous one
   even when the payloads were never recorded. Used only as a fallback, so a
   pipeline that does record its payloads gets the precise graph instead.
"""

from __future__ import annotations

from typing import Any

from tracelens.models import Span, Trace

from ..evaluation.text import flatten_text, overlap

#: How much of a span's output must reappear in another's input before the
#: second is considered to consume the first. High enough that two stages
#: merely discussing the same topic do not look like a data flow.
DATA_FLOW_THRESHOLD = 0.5


def _hashable(value: Any) -> Any | None:
    """A comparable form of a payload value, or None if it has no useful one."""
    if isinstance(value, str):
        stripped = value.strip()
        # Very short values (flags, empty strings) collide constantly and would
        # link unrelated stages.
        return stripped.casefold() if len(stripped) > 3 else None
    if isinstance(value, bool | int | float):
        return None  # numbers alone are far too collision-prone to link stages
    if isinstance(value, list | tuple):
        return tuple(str(v) for v in value) if value else None
    if isinstance(value, dict):
        return tuple(sorted((k, str(v)) for k, v in value.items())) if value else None
    return None


def shares_data(producer: Span, consumer: Span) -> bool:
    """Whether ``consumer`` appears to have read ``producer``'s output."""
    outputs, inputs = producer.outputs, consumer.inputs
    if not outputs or not inputs:
        return False

    produced = {v for v in (_hashable(x) for x in outputs.values()) if v is not None}
    consumed = {v for v in (_hashable(x) for x in inputs.values()) if v is not None}
    if produced & consumed:
        return True

    output_text = flatten_text(outputs)
    input_text = flatten_text(inputs)
    if not output_text or not input_text:
        return False
    return overlap(output_text, input_text) >= DATA_FLOW_THRESHOLD


def ancestors(trace: Trace, span_id: str) -> set[str]:
    """Every span this one is nested inside."""
    found: set[str] = set()
    cursor = trace.span(span_id)
    while cursor is not None and cursor.parent_span_id is not None:
        if cursor.parent_span_id in found:
            break  # already guarded against by the model, belt and braces
        found.add(cursor.parent_span_id)
        cursor = trace.span(cursor.parent_span_id)
    return found


def direct_dependencies(trace: Trace) -> dict[str, set[str]]:
    """Map each span to the spans it consumed directly."""
    ordered = trace.ordered_spans()
    edges: dict[str, set[str]] = {span.span_id: set() for span in ordered}

    for index, consumer in enumerate(ordered):
        earlier = ordered[:index]
        nested_in = ancestors(trace, consumer.span_id)

        for producer in earlier:
            if producer.span_id in nested_in or shares_data(producer, consumer):
                edges[consumer.span_id].add(producer.span_id)

        if not edges[consumer.span_id] and earlier:
            # Fallback: assume the linear pipeline. Only used when neither
            # nesting nor recorded payloads revealed the link.
            edges[consumer.span_id].add(earlier[-1].span_id)

    _link_producers_with_no_consumers(ordered, edges)
    return edges


def _link_producers_with_no_consumers(ordered: list[Span], edges: dict[str, set[str]]) -> None:
    """Apply the sequential fallback from the producer's end as well.

    A span that recorded no output — because it failed, or because it was not
    instrumented — cannot be linked to anything by data flow, however
    thoroughly it broke the run. A timed-out tool call is the clearest case:
    the very reason the next stage misbehaves is that nothing came back, and
    that absence leaves no trace to match on.

    So the same linear-pipeline assumption used for a consumer with no
    producers is applied to a producer with no consumers: link it forward to
    the span that ran next. Without this, the stage that failed hardest is the
    one most likely to be misreported as unrelated.
    """
    has_consumer = {producer for producers in edges.values() for producer in producers}
    for index, producer in enumerate(ordered[:-1]):
        if producer.span_id in has_consumer:
            continue
        edges[ordered[index + 1].span_id].add(producer.span_id)


def downstream_of(trace: Trace, span_id: str) -> set[str]:
    """Every span that transitively consumed ``span_id``'s output."""
    edges = direct_dependencies(trace)
    return downstream_from_edges(edges, span_id)


def downstream_from_edges(edges: dict[str, set[str]], span_id: str) -> set[str]:
    consumers: dict[str, set[str]] = {sid: set() for sid in edges}
    for consumer, producers in edges.items():
        for producer in producers:
            if producer in consumers:
                consumers[producer].add(consumer)

    reached: set[str] = set()
    frontier = list(consumers.get(span_id, ()))
    while frontier:
        current = frontier.pop()
        if current in reached:
            continue
        reached.add(current)
        frontier.extend(consumers.get(current, ()))
    return reached


def validate_stage_transition(producer: Span, consumer: Span) -> list[str]:
    """Problems in the handoff between two adjacent stages.

    Reports what was dropped rather than judging whether dropping it was
    wrong: a summariser legitimately drops fields, and only the pipeline's own
    invariants know which ones had to survive.
    """
    problems: list[str] = []

    if consumer.start_time < producer.start_time:
        problems.append(
            f"{consumer.name} started before {producer.name}, so it cannot have consumed it"
        )

    if producer.outputs and not consumer.inputs:
        problems.append(
            f"{producer.name} produced output but {consumer.name} recorded no inputs, "
            f"so the handoff cannot be verified"
        )
    elif producer.outputs and consumer.inputs and not shares_data(producer, consumer):
        problems.append(f"none of {producer.name}'s output appears in {consumer.name}'s input")

    dropped = sorted(set(producer.outputs) - set(consumer.inputs) - set(consumer.outputs))
    if dropped:
        problems.append(f"fields not carried forward: {', '.join(dropped)}")

    return problems
