"""The first-divergence engine (Phase 7).

The question this module answers is the one the whole project exists for:

    Where did the pipeline first deviate from the expected or internally
    consistent state?

Not "what is broken" — a failing pipeline usually has several broken-looking
stages, and all but one of them are broken *because* of the first one. The
work here is separating those three populations:

    root-cause candidate     the earliest stage whose problem is its own
    downstream consequence   a stage whose problem is explained by an upstream one
    unrelated anomaly        a real problem on a stage that does not depend on the first

The separation rests on two things: execution order, and the dependency graph
from :mod:`.dependencies`. Order alone is not enough — the earliest anomaly in
a trace may sit in a parallel branch that never fed the failure.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from tracelens.models import Stage, Trace

from ..detection import DetectionConfig, FailureCandidate, FailureCategory, run_detectors
from ..invariants import InvariantRegistry, default_registry, run_invariants
from .dependencies import direct_dependencies, downstream_from_edges

#: Categories that can *start* a failure. The rest describe symptoms: a slow
#: stage or a malformed trace is worth reporting and corroborating with, but
#: naming either as the root cause of a wrong answer would be a bad diagnosis.
ORIGINATING_CATEGORIES = frozenset(
    {
        FailureCategory.EXECUTION_ERROR,
        FailureCategory.SCHEMA_VIOLATION,
        FailureCategory.MISSING_INFORMATION,
        FailureCategory.SEMANTIC_INCONSISTENCY,
        FailureCategory.RETRIEVAL_FAILURE,
        FailureCategory.UNSUPPORTED_CLAIM,
        FailureCategory.INVARIANT_VIOLATION,
    }
)

#: A finding weaker than this is not enough to accuse a stage of originating a
#: failure on its own. It still appears in the report and still corroborates.
MIN_ORIGINATING_WEIGHT = 0.15


class Verdict(StrEnum):
    HEALTHY = "healthy"
    ROOT_CAUSE_CANDIDATE = "root_cause_candidate"
    DOWNSTREAM_CONSEQUENCE = "downstream_consequence"
    UNRELATED_ANOMALY = "unrelated_anomaly"


class SpanAssessment(BaseModel):
    """What the engine concluded about one span."""

    model_config = ConfigDict(extra="forbid")

    span_id: str
    span_name: str
    stage: Stage
    verdict: Verdict
    candidates: list[FailureCandidate] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    explanation: str = ""

    @property
    def weight(self) -> float:
        return max((c.weight for c in self.candidates), default=0.0)


class DivergenceReport(BaseModel):
    """Where the pipeline first went wrong, and what followed from it."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    healthy: bool
    first_divergence_span_id: str | None = None
    first_divergence_stage: Stage | None = None
    #: In execution order, so the report reads like the run happened.
    assessments: list[SpanAssessment] = Field(default_factory=list)
    explanation: str = ""

    def assessment(self, span_id: str) -> SpanAssessment | None:
        return next((a for a in self.assessments if a.span_id == span_id), None)

    @property
    def first_divergence(self) -> SpanAssessment | None:
        if self.first_divergence_span_id is None:
            return None
        return self.assessment(self.first_divergence_span_id)

    @property
    def downstream(self) -> list[SpanAssessment]:
        return [a for a in self.assessments if a.verdict is Verdict.DOWNSTREAM_CONSEQUENCE]

    @property
    def unrelated(self) -> list[SpanAssessment]:
        return [a for a in self.assessments if a.verdict is Verdict.UNRELATED_ANOMALY]

    @property
    def all_candidates(self) -> list[FailureCandidate]:
        return [c for a in self.assessments for c in a.candidates]


def collect_candidates(
    trace: Trace,
    config: DetectionConfig | None = None,
    registry: InvariantRegistry | None = None,
) -> list[FailureCandidate]:
    """Every finding about a trace, from detectors and invariants alike.

    Both sources speak the same language by the time they get here, which is
    what lets the divergence engine reason over them uniformly.
    """
    violations = run_invariants(trace, registry if registry is not None else default_registry())
    return [*run_detectors(trace, config), *(v.to_candidate() for v in violations)]


def find_first_divergence(
    trace: Trace,
    candidates: list[FailureCandidate] | None = None,
    config: DetectionConfig | None = None,
    registry: InvariantRegistry | None = None,
) -> DivergenceReport:
    """Locate the earliest stage whose failure is its own, not inherited.

    The walk is:

    1. Attach every finding to its span.
    2. Walk spans in execution order and take the first whose findings include
       an *originating* category with enough evidence to stand on. Earlier
       spans carrying only corroborating findings (a latency blip) do not
       qualify — they are reported, but they did not start this.
    3. Everything after it that transitively consumed its output is a
       downstream consequence.
    4. Everything after it that did not is an unrelated anomaly, reported
       separately rather than folded into the diagnosis.
    """
    if candidates is None:
        candidates = collect_candidates(trace, config, registry)

    ordered = trace.ordered_spans()
    by_span: dict[str, list[FailureCandidate]] = {span.span_id: [] for span in ordered}
    orphaned = [c for c in candidates if c.span_id not in by_span]
    for candidate in candidates:
        if candidate.span_id in by_span:
            by_span[candidate.span_id].append(candidate)

    edges = direct_dependencies(trace)

    first = _first_originating_span(ordered, by_span)
    if first is None:
        return _healthy_report(trace, ordered, by_span, edges, orphaned)

    downstream = downstream_from_edges(edges, first)
    assessments = []
    for span in ordered:
        found = by_span[span.span_id]
        assessments.append(
            SpanAssessment(
                span_id=span.span_id,
                span_name=span.name,
                stage=span.stage,
                verdict=_verdict_for(span.span_id, found, first, downstream),
                candidates=found,
                depends_on=sorted(edges.get(span.span_id, set())),
                explanation=_span_explanation(span.span_id, found, first, downstream),
            )
        )

    report = DivergenceReport(
        trace_id=trace.trace_id,
        healthy=False,
        first_divergence_span_id=first,
        first_divergence_stage=next(s.stage for s in ordered if s.span_id == first),
        assessments=assessments,
    )
    report.explanation = _report_explanation(report, orphaned)
    return report


def _first_originating_span(
    ordered: list, by_span: dict[str, list[FailureCandidate]]
) -> str | None:
    """The earliest span carrying a finding strong enough to originate a failure."""
    for span in ordered:
        if any(_can_originate(c) for c in by_span[span.span_id]):
            return str(span.span_id)
    return None


def _can_originate(candidate: FailureCandidate) -> bool:
    return (
        candidate.category in ORIGINATING_CATEGORIES and candidate.weight >= MIN_ORIGINATING_WEIGHT
    )


def _verdict_for(
    span_id: str,
    found: list[FailureCandidate],
    first: str,
    downstream: set[str],
) -> Verdict:
    if span_id == first:
        return Verdict.ROOT_CAUSE_CANDIDATE
    if not found:
        return Verdict.HEALTHY
    if span_id in downstream:
        return Verdict.DOWNSTREAM_CONSEQUENCE
    return Verdict.UNRELATED_ANOMALY


def _span_explanation(
    span_id: str,
    found: list[FailureCandidate],
    first: str,
    downstream: set[str],
) -> str:
    if span_id == first:
        return "earliest stage whose problem is not explained by anything upstream"
    if not found:
        return "no findings"
    if span_id in downstream:
        return "problem is consistent with consuming the diverged output from upstream"
    return "problem does not depend on the first divergence; reported separately"


def _healthy_report(
    trace: Trace,
    ordered: list,
    by_span: dict[str, list[FailureCandidate]],
    edges: dict[str, set[str]],
    orphaned: list[FailureCandidate],
) -> DivergenceReport:
    """No stage originated a failure.

    The trace may still carry corroborating findings — a slow span, a
    structural gap — so they are reported as unrelated anomalies rather than
    silently dropped. "Healthy" here means "nothing diverged", not "nothing
    was observed".
    """
    assessments = [
        SpanAssessment(
            span_id=span.span_id,
            span_name=span.name,
            stage=span.stage,
            verdict=Verdict.HEALTHY if not by_span[span.span_id] else Verdict.UNRELATED_ANOMALY,
            candidates=by_span[span.span_id],
            depends_on=sorted(edges.get(span.span_id, set())),
            explanation=(
                "no findings"
                if not by_span[span.span_id]
                else "an anomaly was observed, but nothing indicates the pipeline diverged"
            ),
        )
        for span in ordered
    ]
    noted = sum(len(a.candidates) for a in assessments) + len(orphaned)
    return DivergenceReport(
        trace_id=trace.trace_id,
        healthy=True,
        assessments=assessments,
        explanation=(
            "No stage diverged: every finding is either absent or corroborating only."
            if noted
            else "No stage diverged and no findings were recorded."
        ),
    )


def _report_explanation(report: DivergenceReport, orphaned: list[FailureCandidate]) -> str:
    first = report.first_divergence
    if first is None:
        return "No divergence identified."

    lines = [
        f"First divergence at '{first.span_name}' ({first.stage.value}): "
        f"{first.candidates[0].summary}"
        if first.candidates
        else f"First divergence at '{first.span_name}' ({first.stage.value})."
    ]

    downstream = report.downstream
    if downstream:
        lines.append(
            "Downstream consequence"
            + ("s" if len(downstream) > 1 else "")
            + ": "
            + ", ".join(f"{a.span_name} ({a.stage.value})" for a in downstream)
            + "."
        )
    else:
        lines.append("No downstream stage recorded a further problem.")

    unrelated = report.unrelated
    if unrelated:
        lines.append(
            "Unrelated anomal"
            + ("ies" if len(unrelated) > 1 else "y")
            + " on: "
            + ", ".join(f"{a.span_name} ({a.stage.value})" for a in unrelated)
            + ", which did not consume the diverged output."
        )
    if orphaned:
        lines.append(
            f"{len(orphaned)} trace-level finding(s) not attached to any span: "
            + "; ".join(c.summary for c in orphaned[:3])
            + "."
        )
    return " ".join(lines)
