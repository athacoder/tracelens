"""The root-cause report: the artefact a human actually reads.

A ranked list of scores is not a diagnosis. What makes a diagnosis convincing
is the chain: this is what went wrong, here is the evidence, here is why the
stages that came after it are *not* to blame, and here is what it cost.

That third element is the one usually missing. A report that says "the
retriever is at fault" invites the question "how do you know it wasn't the
model?", and the answer — "because the model's output is consistent with the
prompt it was given, and the prompt is consistent with what was retrieved" —
is exculpatory evidence. This module generates it explicitly.
"""

from __future__ import annotations

import time
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from tracelens.models import Stage, Trace, utcnow

from ..detection import DetectionConfig
from ..detection.models import Evidence, EvidenceKind, FailureCategory
from ..invariants import InvariantRegistry
from .dependencies import downstream_from_edges
from .divergence import DivergenceReport, Verdict, find_first_divergence
from .scoring import RootCauseCandidate, rank_failure_candidates

#: What to do about each kind of failure. Deliberately about the pipeline, not
#: about the code: TraceLens can see which stage diverged, not which line did.
REMEDIATION: dict[FailureCategory, str] = {
    FailureCategory.RETRIEVAL_FAILURE: (
        "Review the retriever: check index freshness, the filter that should exclude "
        "superseded documents, and whether the query reaches the index intact."
    ),
    FailureCategory.EXECUTION_ERROR: (
        "Handle the failing call: add a timeout and retry policy, and decide what the "
        "pipeline should answer when this dependency is unavailable."
    ),
    FailureCategory.SCHEMA_VIOLATION: (
        "Enforce the stage's output contract at its boundary so a malformed payload "
        "fails here rather than being interpreted downstream."
    ),
    FailureCategory.MISSING_INFORMATION: (
        "Trace where the value is dropped between stages and assert its presence at the handoff."
    ),
    FailureCategory.SEMANTIC_INCONSISTENCY: (
        "Compare the stage's input and output directly. If this is the model, tighten "
        "the prompt's grounding instruction; if it is a transform, fix the transform."
    ),
    FailureCategory.UNSUPPORTED_CLAIM: (
        "Add a grounding check before the answer is returned, rejecting claims that no "
        "retrieved document or tool result supports."
    ),
    FailureCategory.INVARIANT_VIOLATION: (
        "Find the stage that changed the value and make the handoff preserve it, or "
        "correct the invariant if the change is legitimate."
    ),
    FailureCategory.LATENCY_ANOMALY: (
        "Record per-stage latency baselines so this can be judged against history "
        "rather than against the rest of one trace."
    ),
    FailureCategory.STRUCTURAL_ANOMALY: (
        "Complete the instrumentation: an incomplete trace limits what any analysis can conclude."
    ),
}


class RootCauseReport(BaseModel):
    """The full forensic answer for one trace."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    trace_name: str
    project: str
    pipeline: str
    generated_at: datetime = Field(default_factory=utcnow)
    healthy: bool

    likely_root_cause: RootCauseCandidate | None = None
    ranked_candidates: list[RootCauseCandidate] = Field(default_factory=list)
    first_divergence_span_id: str | None = None
    first_divergence_stage: Stage | None = None

    #: Ordered narrative: what broke, why the following stages did not, and
    #: what the failure cost.
    evidence_chain: list[Evidence] = Field(default_factory=list)
    downstream_impact: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    summary: str = ""

    divergence: DivergenceReport
    #: Wall-clock cost of the analysis, reported so section 32's latency claim
    #: is measured rather than asserted.
    analysis_ms: float = 0.0

    @property
    def diagnostic_confidence(self) -> float:
        return self.likely_root_cause.confidence if self.likely_root_cause else 0.0


def build_evidence_chain(trace: Trace, divergence: DivergenceReport) -> list[Evidence]:
    """Assemble the narrative that supports the diagnosis.

    Three passes, in the order a person reads them:

    1. What the diverging stage did wrong.
    2. Why each downstream stage that behaved correctly is not to blame. This
       is what turns a claim into an argument.
    3. What the downstream stages that did misbehave inherited.
    """
    first = divergence.first_divergence
    if first is None:
        return []

    chain: list[Evidence] = []
    for candidate in sorted(first.candidates, key=lambda c: -c.weight):
        chain.extend(candidate.evidence)

    edges = {a.span_id: set(a.depends_on) for a in divergence.assessments}
    affected = downstream_from_edges(edges, first.span_id)

    for assessment in divergence.assessments:
        if assessment.span_id not in affected:
            continue
        if assessment.candidates:
            chain.append(
                Evidence(
                    kind=EvidenceKind.COMPARISON,
                    description=(
                        f"{assessment.span_name} ({assessment.stage.value}) then failed: "
                        f"{assessment.candidates[0].summary}"
                    ),
                    span_id=assessment.span_id,
                    stage=assessment.stage,
                    detail={"role": "downstream consequence"},
                )
            )
        else:
            chain.append(
                Evidence(
                    kind=EvidenceKind.OBSERVED,
                    description=_exculpatory(assessment.span_name, assessment.stage),
                    span_id=assessment.span_id,
                    stage=assessment.stage,
                    detail={"role": "exculpatory"},
                )
            )

    return chain


def _exculpatory(span_name: str, stage: Stage) -> str:
    """Say specifically why this stage is cleared, not merely that it is."""
    by_stage = {
        Stage.PROMPT_BUILD: (
            f"{span_name} carried the retrieved content into the prompt unchanged, so the "
            f"prompt reflects what retrieval returned"
        ),
        Stage.LLM: (
            f"{span_name} produced an answer consistent with the prompt it was given, so the "
            f"model followed its evidence"
        ),
        Stage.POSTPROCESSING: (
            f"{span_name} passed the answer through without altering any of its values"
        ),
        Stage.VALIDATION: f"{span_name} raised no validation failure",
        Stage.TOOL: f"{span_name} returned a result consistent with its inputs",
    }
    return by_stage.get(stage, f"{span_name} ({stage.value}) reported no problem of its own")


def generate_root_cause_report(
    trace: Trace,
    config: DetectionConfig | None = None,
    registry: InvariantRegistry | None = None,
    divergence: DivergenceReport | None = None,
) -> RootCauseReport:
    """Run the full forensic pass over a trace and render the answer."""
    started = time.perf_counter()

    if divergence is None:
        divergence = find_first_divergence(trace, config=config, registry=registry)
    ranked = rank_failure_candidates(trace, divergence)
    likely = ranked[0] if ranked and not divergence.healthy else None
    chain = build_evidence_chain(trace, divergence)

    report = RootCauseReport(
        trace_id=trace.trace_id,
        trace_name=trace.name,
        project=trace.project,
        pipeline=trace.pipeline,
        healthy=divergence.healthy,
        likely_root_cause=likely,
        ranked_candidates=ranked,
        first_divergence_span_id=divergence.first_divergence_span_id,
        first_divergence_stage=divergence.first_divergence_stage,
        evidence_chain=chain,
        downstream_impact=likely.downstream_effects if likely else [],
        recommended_actions=_recommendations(likely),
        summary=_summary(trace, divergence, likely),
        divergence=divergence,
        analysis_ms=(time.perf_counter() - started) * 1000.0,
    )
    return report


def _recommendations(likely: RootCauseCandidate | None) -> list[str]:
    if likely is None:
        return []
    # De-duplicated but order-preserving: the strongest finding's remedy first.
    seen: dict[str, None] = {}
    for candidate in sorted(likely.candidates, key=lambda c: -c.weight):
        advice = REMEDIATION.get(candidate.category)
        if advice:
            seen.setdefault(advice, None)
    return list(seen)


def _summary(
    trace: Trace,
    divergence: DivergenceReport,
    likely: RootCauseCandidate | None,
) -> str:
    if likely is None:
        return f"No divergence found in '{trace.name}'. {divergence.explanation}"

    parts = [
        f"Likely root cause: {likely.span_name} ({likely.stage.value}), "
        f"diagnostic score {likely.score:.2f}, confidence {likely.confidence:.2f}.",
        likely.summary + ".",
    ]
    if likely.downstream_effects:
        parts.append(f"{len(likely.downstream_effects)} downstream stage(s) were affected.")
    others = [c for c in divergence.assessments if c.verdict is Verdict.UNRELATED_ANOMALY]
    if others:
        parts.append(
            f"{len(others)} unrelated anomal{'ies' if len(others) > 1 else 'y'} were also "
            f"observed and are reported separately."
        )
    return " ".join(parts)
