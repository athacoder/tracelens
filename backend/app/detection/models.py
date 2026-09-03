"""The failure taxonomy and the structures detectors return.

A detector's job is not to be right. It is to make an observation that a human
can check, attach the span it came from, and state how much the observation is
worth. Ranking (Phase 8) is what turns a pile of observations into a diagnosis,
and it can only do that if every candidate is honest about its own strength.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from tracelens.models import Severity, Stage


class FailureCategory(StrEnum):
    """What kind of thing went wrong.

    Kept small and mutually distinguishable: each category maps to a different
    remediation. Splitting further would produce labels nobody can apply
    consistently, which shows up immediately as noise in benchmark scoring.
    """

    #: The stage raised or reported an error. Directly observed.
    EXECUTION_ERROR = "execution_error"
    #: A payload did not have the shape the next stage needs.
    SCHEMA_VIOLATION = "schema_violation"
    #: Something required was absent or empty.
    MISSING_INFORMATION = "missing_information"
    #: A stage took long enough to be worth questioning.
    LATENCY_ANOMALY = "latency_anomaly"
    #: A stage's output contradicts or is unsupported by its own input.
    SEMANTIC_INCONSISTENCY = "semantic_inconsistency"
    #: Retrieval returned nothing, the wrong material, or stale material.
    RETRIEVAL_FAILURE = "retrieval_failure"
    #: The final answer asserts something no source supports.
    UNSUPPORTED_CLAIM = "unsupported_claim"
    #: A registered pipeline invariant was violated.
    INVARIANT_VIOLATION = "invariant_violation"
    #: The trace itself is malformed: missing parents, spans never closed.
    STRUCTURAL_ANOMALY = "structural_anomaly"


class EvidenceKind(StrEnum):
    """How an observation was obtained, which is what sets its weight.

    Observed facts outrank rule violations, which outrank heuristics. Phase 8
    uses this ordering directly, so it is data rather than a comment.
    """

    OBSERVED = "observed"
    RULE = "rule"
    COMPARISON = "comparison"
    HEURISTIC = "heuristic"

    @property
    def weight(self) -> float:
        return {"observed": 1.0, "rule": 0.8, "comparison": 0.65, "heuristic": 0.4}[self.value]


class Evidence(BaseModel):
    """One checkable observation, anchored to the span it came from."""

    model_config = ConfigDict(extra="forbid")

    kind: EvidenceKind
    description: str
    span_id: str | None = None
    stage: Stage | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

    def __str__(self) -> str:
        return self.description


class FailureCandidate(BaseModel):
    """A detector's finding about one span."""

    model_config = ConfigDict(extra="forbid")

    detector: str
    category: FailureCategory
    severity: Severity
    #: How much the detector trusts its own finding, anchored to evidence
    #: provenance (D-008). Never rounded up to hide uncertainty.
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    span_id: str | None = None
    stage: Stage = Stage.OTHER
    evidence: list[Evidence] = Field(default_factory=list)

    @property
    def evidence_strength(self) -> float:
        """Best available evidence, damped by how much of it agrees.

        Taking the maximum rather than the mean keeps one directly observed
        fact from being diluted by weaker corroboration, while the corroboration
        bonus still rewards independent agreement.
        """
        if not self.evidence:
            return 0.0
        best = max(e.kind.weight for e in self.evidence)
        corroboration = min(len(self.evidence) - 1, 3) * 0.05
        return min(1.0, best + corroboration)

    @property
    def weight(self) -> float:
        """Single comparable number combining severity, confidence, evidence."""
        return self.severity.weight * self.confidence * self.evidence_strength

    def __str__(self) -> str:
        return f"[{self.category}] {self.summary} (confidence {self.confidence:.2f})"
