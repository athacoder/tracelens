"""Root-cause ranking (Phase 8).

Ranking turns a set of findings into an ordered answer to "what should I fix
first?". The score here is a **diagnostic score**, not a probability. Nothing
in this project has been calibrated against a population of real incidents, so
calling 0.92 a 92% chance would be a lie that happens to sound rigorous
(CLAUDE.md §8).

What the score does promise is that it is auditable: every factor that went
into it is recorded on the candidate under ``score_components``, so a number
can always be taken apart and argued with.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from tracelens.models import Stage, Trace

from ..detection.models import Evidence, FailureCandidate
from .dependencies import downstream_from_edges
from .divergence import DivergenceReport, Verdict, find_first_divergence

#: How much a span's position in the causal chain counts. The first divergence
#: is what the whole analysis is for; a stage that merely inherited a bad input
#: is heavily discounted, because "fix the thing that consumed the bad data" is
#: the wrong instruction.
POSITION_WEIGHTS: dict[Verdict, float] = {
    Verdict.ROOT_CAUSE_CANDIDATE: 1.0,
    Verdict.UNRELATED_ANOMALY: 0.60,
    Verdict.DOWNSTREAM_CONSEQUENCE: 0.35,
    Verdict.HEALTHY: 0.0,
}

#: Independent detectors agreeing is real corroboration, but it saturates
#: quickly: the fourth detector noticing the same empty list adds little.
AGREEMENT_STEP = 0.08
MAX_AGREEMENT = 1.24

#: Breaking more downstream stages makes a fix more urgent, not more certain.
IMPACT_STEP = 0.04
MAX_IMPACT = 1.20


class RootCauseCandidate(BaseModel):
    """One stage, scored and ranked as a possible root cause."""

    model_config = ConfigDict(extra="forbid")

    rank: int
    span_id: str
    span_name: str
    stage: Stage
    verdict: Verdict
    #: Comparable within one trace. Not a probability (see module docstring).
    score: float = Field(ge=0.0, le=1.0)
    #: How much this candidate dominates the alternatives, tempered by the
    #: quality of its evidence. Also not a probability.
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    candidates: list[FailureCandidate] = Field(default_factory=list)
    downstream_effects: list[str] = Field(default_factory=list)
    #: Every factor behind ``score``, so the number can be taken apart.
    score_components: dict[str, float] = Field(default_factory=dict)
    explanation: str = ""

    @property
    def detectors(self) -> list[str]:
        return sorted({c.detector for c in self.candidates})


def rank_failure_candidates(
    trace: Trace,
    divergence: DivergenceReport | None = None,
) -> list[RootCauseCandidate]:
    """Order the stages that carry findings by how likely each is the cause.

    The score multiplies four factors, each of which answers a different
    question:

    ``base``        how bad is the worst finding here (severity x confidence
                    x evidence strength, from the detector itself)
    ``position``    did this stage start the failure or inherit it
    ``agreement``   did independent detectors reach the same conclusion
    ``impact``      how much of the pipeline consumed this stage's output

    Multiplication rather than a weighted sum, because these are not
    interchangeable: a downstream stage with overwhelming evidence still is
    not the root cause, and no amount of detector agreement should rescue a
    finding with no evidence behind it.
    """
    if divergence is None:
        divergence = find_first_divergence(trace)

    scored: list[RootCauseCandidate] = []

    for assessment in divergence.assessments:
        if not assessment.candidates:
            continue

        base = max(c.weight for c in assessment.candidates)
        position = POSITION_WEIGHTS[assessment.verdict]
        agreement = min(
            MAX_AGREEMENT,
            1.0 + AGREEMENT_STEP * (len({c.detector for c in assessment.candidates}) - 1),
        )
        downstream = _downstream_names(divergence, assessment.span_id)
        impact = min(MAX_IMPACT, 1.0 + IMPACT_STEP * len(downstream))

        score = min(1.0, base * position * agreement * impact)
        strongest = max(assessment.candidates, key=lambda c: c.weight)

        scored.append(
            RootCauseCandidate(
                rank=0,  # assigned once the whole list is sorted
                span_id=assessment.span_id,
                span_name=assessment.span_name,
                stage=assessment.stage,
                verdict=assessment.verdict,
                score=score,
                confidence=0.0,  # needs the full set; filled in below
                summary=strongest.summary,
                evidence=[e for c in assessment.candidates for e in c.evidence],
                candidates=list(assessment.candidates),
                downstream_effects=downstream,
                score_components={
                    "base": round(base, 4),
                    "position": position,
                    "agreement": round(agreement, 4),
                    "impact": round(impact, 4),
                },
                explanation=assessment.explanation,
            )
        )

    scored.sort(key=lambda c: (-c.score, c.stage.position))
    total = sum(c.score for c in scored)

    for index, candidate in enumerate(scored, start=1):
        candidate.rank = index
        candidate.confidence = _confidence(candidate, total)

    return scored


def _confidence(candidate: RootCauseCandidate, total_score: float) -> float:
    """How much this candidate dominates the field, tempered by its evidence.

    A lone finding backed only by a heuristic should not report high
    confidence merely because nothing competed with it, so dominance is
    multiplied by the strength of the evidence rather than reported alone.
    """
    if total_score <= 0:
        return 0.0
    dominance = candidate.score / total_score
    evidence_strength = max(
        (c.evidence_strength for c in candidate.candidates),
        default=0.0,
    )
    return round(min(1.0, dominance * evidence_strength), 4)


def _downstream_names(divergence: DivergenceReport, span_id: str) -> list[str]:
    """Stages that consumed this span's output, directly or transitively, and
    reported a problem of their own.

    Transitive, not direct: a corrupted retrieval reaches the answer through
    the prompt builder, and an impact list that stopped at the prompt would
    understate what the failure actually cost.
    """
    edges = {a.span_id: set(a.depends_on) for a in divergence.assessments}
    reachable = downstream_from_edges(edges, span_id)
    return [
        f"{a.span_name} ({a.stage.value}): {a.candidates[0].summary}"
        for a in divergence.assessments
        if a.span_id in reachable and a.candidates
    ]
