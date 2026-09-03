"""What an invariant is and what it produces when broken.

A detector asks "does this look wrong?". An invariant asks "did this pipeline
break a rule its own author declared?". The second question is much easier to
answer and much harder to argue with, which is why invariant violations carry
more weight in ranking than heuristic findings do.

An invariant is a named, described, severity-bearing predicate over a trace.
The name and description are not decoration: they are what a violation report
shows the engineer who has to fix it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from tracelens.models import Severity, Stage, Trace

from ..detection.models import Evidence, EvidenceKind, FailureCandidate, FailureCategory


class InvariantViolation(BaseModel):
    """One broken rule, with the observations that broke it."""

    model_config = ConfigDict(extra="forbid")

    invariant: str
    description: str
    severity: Severity
    summary: str
    span_id: str | None = None
    stage: Stage = Stage.OTHER
    #: Where the offending values were seen, keyed by span name. This is what
    #: makes a violation explainable rather than merely reported.
    observations: dict[str, Any] = Field(default_factory=dict)
    detail: dict[str, Any] = Field(default_factory=dict)

    def to_candidate(self) -> FailureCandidate:
        """Express the violation in the common currency of the ranking stage.

        Confidence is 0.95 rather than 1.0: the rule was definitely broken, but
        a rule can be declared wrongly, and the engine should not be more
        certain than the person who wrote the invariant.
        """
        return FailureCandidate(
            detector=f"invariant:{self.invariant}",
            category=FailureCategory.INVARIANT_VIOLATION,
            severity=self.severity,
            confidence=0.95,
            summary=self.summary,
            span_id=self.span_id,
            stage=self.stage,
            evidence=[
                Evidence(
                    kind=EvidenceKind.RULE,
                    description=self.summary,
                    span_id=self.span_id,
                    stage=self.stage,
                    detail={
                        "invariant": self.invariant,
                        "description": self.description,
                        "observations": {k: str(v)[:200] for k, v in self.observations.items()},
                        **self.detail,
                    },
                )
            ],
        )


@dataclass(frozen=True)
class Invariant:
    """A rule the pipeline is expected to hold, and the check that proves it."""

    name: str
    description: str
    severity: Severity
    check: Callable[[Trace], list[InvariantViolation]]

    def run(self, trace: Trace) -> list[InvariantViolation]:
        return self.check(trace)


def explain_violation(violation: InvariantViolation) -> str:
    """Render a violation as the paragraph a human needs to act on it.

    States the rule, then where it broke, then what was seen at each point.
    """
    lines = [
        f"Invariant '{violation.invariant}' was violated ({violation.severity.value}).",
        f"Rule: {violation.description}",
        f"What happened: {violation.summary}",
    ]
    if violation.observations:
        lines.append("Observed values:")
        lines.extend(f"  - {where}: {value!r}" for where, value in violation.observations.items())
    return "\n".join(lines)
