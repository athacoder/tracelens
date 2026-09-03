"""Replay and run comparison (Phase 13).

TraceLens cannot re-execute a user's pipeline on its own — it records what
happened, it does not own the code that happened. So replay is split into the
part that needs the caller's cooperation and the part that does not:

``replay_trace``  re-runs a stored trace through a ``runner`` the caller
                  supplies, which is the only thing that can actually execute
                  their pipeline.
``compare_runs``  diffs two traces stage by stage. Needs nothing but the two
                  traces, and is where the value is: it answers "what changed
                  between the run that worked and the run that doesn't", which
                  is the question regression debugging actually asks.

The comparison reports the *first* stage whose behaviour differs, deliberately
mirroring first-divergence: in a regression, the earliest changed stage is the
one worth looking at, and every later difference is usually its consequence.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from tracelens.models import Span, Stage, Trace

from ..storage.repository import TraceRepository

#: Payload keys that legitimately differ between two runs of the same input.
#: Comparing them would report every replay as changed.
VOLATILE_KEYS = frozenset({"timestamp", "trace_id", "span_id", "request_id", "run_id"})

#: How much slower a stage must get before the difference is worth reporting.
#: Wall-clock noise on a millisecond-scale stage is not a regression.
LATENCY_NOISE_MS = 5.0
LATENCY_RATIO = 1.5


class SpanDiff(BaseModel):
    """How one stage differed between two runs."""

    model_config = ConfigDict(extra="forbid")

    span_name: str
    stage: Stage
    in_original: bool = True
    in_replay: bool = True
    inputs_changed: bool = False
    outputs_changed: bool = False
    status_changed: bool = False
    error_changed: bool = False
    changed_fields: list[str] = Field(default_factory=list)
    original_duration_ms: float | None = None
    replay_duration_ms: float | None = None

    @property
    def latency_delta_ms(self) -> float | None:
        if self.original_duration_ms is None or self.replay_duration_ms is None:
            return None
        return self.replay_duration_ms - self.original_duration_ms

    @property
    def latency_regressed(self) -> bool:
        delta = self.latency_delta_ms
        if delta is None or delta <= LATENCY_NOISE_MS:
            return False
        original = self.original_duration_ms or 0.0
        return original <= 0 or self.replay_duration_ms >= original * LATENCY_RATIO  # type: ignore[operator]

    @property
    def changed(self) -> bool:
        return (
            not self.in_original
            or not self.in_replay
            or self.inputs_changed
            or self.outputs_changed
            or self.status_changed
            or self.error_changed
        )


class RunComparison(BaseModel):
    """The diff between an original run and its replay."""

    model_config = ConfigDict(extra="forbid")

    original_trace_id: str
    replay_trace_id: str
    identical: bool
    #: The first stage whose behaviour differs. In a regression this is the one
    #: worth investigating; the rest usually follow from it.
    diverged_at: str | None = None
    diverged_stage: Stage | None = None
    span_diffs: list[SpanDiff] = Field(default_factory=list)
    original_duration_ms: float | None = None
    replay_duration_ms: float | None = None
    summary: str = ""

    @property
    def changed_spans(self) -> list[SpanDiff]:
        return [d for d in self.span_diffs if d.changed]

    @property
    def latency_regressions(self) -> list[SpanDiff]:
        return [d for d in self.span_diffs if d.latency_regressed]


def _comparable(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop the keys that differ between any two runs by construction."""
    return {k: v for k, v in payload.items() if k.lower() not in VOLATILE_KEYS}


def _changed_fields(original: dict[str, Any], replay: dict[str, Any]) -> list[str]:
    left, right = _comparable(original), _comparable(replay)
    return sorted({key for key in set(left) | set(right) if left.get(key) != right.get(key)})


def _key(span: Span) -> tuple[str, str]:
    """Identify a span across runs by name and stage, not by id.

    Span ids are regenerated on every run, so matching on them would report two
    identical runs as sharing nothing.
    """
    return (span.name, span.stage.value)


def compare_runs(original: Trace, replay: Trace) -> RunComparison:
    """Diff two traces stage by stage."""
    original_spans = {_key(s): s for s in original.ordered_spans()}
    replay_spans = {_key(s): s for s in replay.ordered_spans()}

    diffs: list[SpanDiff] = []
    # Walk in the original's execution order, then append anything the replay
    # added, so the diff reads in the order the original run happened.
    ordered_keys = [_key(s) for s in original.ordered_spans()]
    ordered_keys += [
        k for k in (_key(s) for s in replay.ordered_spans()) if k not in original_spans
    ]

    for key in ordered_keys:
        before, after = original_spans.get(key), replay_spans.get(key)
        name, stage = key

        if before is None or after is None:
            present = before or after
            diffs.append(
                SpanDiff(
                    span_name=name,
                    stage=Stage(stage),
                    in_original=before is not None,
                    in_replay=after is not None,
                    original_duration_ms=before.duration_ms if before else None,
                    replay_duration_ms=after.duration_ms if after else None,
                    changed_fields=sorted(_comparable(present.outputs)) if present else [],
                )
            )
            continue

        input_changes = _changed_fields(before.inputs, after.inputs)
        output_changes = _changed_fields(before.outputs, after.outputs)
        before_error = before.error.type if before.error else None
        after_error = after.error.type if after.error else None

        diffs.append(
            SpanDiff(
                span_name=name,
                stage=Stage(stage),
                inputs_changed=bool(input_changes),
                outputs_changed=bool(output_changes),
                status_changed=before.status is not after.status,
                error_changed=before_error != after_error,
                changed_fields=sorted(set(input_changes) | set(output_changes)),
                original_duration_ms=before.duration_ms,
                replay_duration_ms=after.duration_ms,
            )
        )

    first_changed = next((d for d in diffs if d.changed), None)
    comparison = RunComparison(
        original_trace_id=original.trace_id,
        replay_trace_id=replay.trace_id,
        identical=first_changed is None,
        diverged_at=first_changed.span_name if first_changed else None,
        diverged_stage=first_changed.stage if first_changed else None,
        span_diffs=diffs,
        original_duration_ms=original.duration_ms,
        replay_duration_ms=replay.duration_ms,
    )
    comparison.summary = _summarise(comparison)
    return comparison


def _summarise(comparison: RunComparison) -> str:
    if comparison.identical:
        note = "The replay reproduced the original run exactly."
        regressions = comparison.latency_regressions
        if regressions:
            note += (
                f" {len(regressions)} stage(s) got slower without changing behaviour: "
                + ", ".join(d.span_name for d in regressions)
                + "."
            )
        return note

    changed = comparison.changed_spans
    parts = [
        f"The runs first differ at '{comparison.diverged_at}' "
        f"({comparison.diverged_stage.value if comparison.diverged_stage else 'unknown'})."
    ]
    first = changed[0]
    if not first.in_replay:
        parts.append("That stage did not run in the replay.")
    elif not first.in_original:
        parts.append("That stage is new in the replay.")
    elif first.changed_fields:
        parts.append(f"Changed fields: {', '.join(first.changed_fields[:6])}.")

    if len(changed) > 1:
        parts.append(
            f"{len(changed) - 1} later stage(s) also differ, most likely as a consequence."
        )
    return " ".join(parts)


def replay_trace(
    session: Session,
    trace_id: str,
    runner: Callable[[Trace], Trace],
) -> RunComparison | None:
    """Re-run a stored trace through ``runner`` and diff the result.

    ``runner`` is supplied by the caller because only they can execute their
    pipeline; TraceLens records runs, it does not own them. It receives the
    original trace, so it can read back the exact inputs each stage was given.

    Returns None when the trace is not stored.
    """
    original = TraceRepository(session).get_trace(trace_id)
    if original is None:
        return None
    return compare_runs(original, runner(original))


def original_inputs(trace: Trace) -> dict[str, dict[str, Any]]:
    """The inputs each stage received, keyed by span name.

    The convenience a ``runner`` needs to drive a replay from a stored trace
    without re-deriving the payload conventions itself.
    """
    return {span.name: dict(span.inputs) for span in trace.ordered_spans()}
