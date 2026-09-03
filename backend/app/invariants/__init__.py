"""The invariant engine (Phase 6).

Rules a pipeline declares about itself, and the machinery that checks them.
Register with :func:`register_invariant`, run with :func:`run_invariants`,
explain with :func:`explain_violation`.
"""

from __future__ import annotations

from collections.abc import Callable

from tracelens.models import Severity, Trace

from .builders import (
    field_present,
    field_stable,
    numeric_within,
    observations_of,
    retrieved_context_relevant,
    tool_results_not_contradicted,
)
from .models import Invariant, InvariantViolation, explain_violation


class InvariantRegistry:
    """A named collection of invariants.

    Registries are explicit objects rather than one global list because
    different pipelines hold different rules, and a benchmark scenario needs to
    run its own set without polluting anyone else's.
    """

    def __init__(self, invariants: list[Invariant] | None = None) -> None:
        self._invariants: dict[str, Invariant] = {}
        for invariant in invariants or []:
            self.register(invariant)

    def register(self, invariant: Invariant) -> Invariant:
        """Add an invariant. Re-registering a name replaces it."""
        self._invariants[invariant.name] = invariant
        return invariant

    def unregister(self, name: str) -> None:
        self._invariants.pop(name, None)

    @property
    def invariants(self) -> list[Invariant]:
        return list(self._invariants.values())

    def __len__(self) -> int:
        return len(self._invariants)

    def __contains__(self, name: object) -> bool:
        return name in self._invariants

    def run(self, trace: Trace) -> list[InvariantViolation]:
        """Check every invariant against a trace.

        An invariant that raises is reported as a violation of itself rather
        than being allowed to abort the run: a broken rule must not hide the
        findings of the rules that still work.
        """
        violations: list[InvariantViolation] = []
        for invariant in self._invariants.values():
            try:
                violations.extend(invariant.run(trace))
            except Exception as error:  # noqa: BLE001 - deliberately broad
                violations.append(
                    InvariantViolation(
                        invariant=invariant.name,
                        description=invariant.description,
                        severity=Severity.LOW,
                        summary=(
                            f"invariant '{invariant.name}' could not be evaluated: "
                            f"{type(error).__name__}: {error}"
                        ),
                        detail={"error_type": type(error).__name__},
                    )
                )
        return violations


def default_registry() -> InvariantRegistry:
    """Invariants that are safe for any pipeline.

    Every one of these is conditional on the field actually appearing in at
    least two places, so a pipeline that never emits ``user_id`` is never
    penalised for it. Rules with any pipeline-specific assumption belong in a
    caller-supplied registry, not here.
    """
    return InvariantRegistry(
        [
            field_stable("user_id", Severity.CRITICAL),
            field_stable("session_id", Severity.HIGH),
            field_stable("tenant_id", Severity.CRITICAL),
            field_stable("document_id", Severity.HIGH),
            field_stable("currency", Severity.CRITICAL),
            field_stable("order_id", Severity.HIGH),
            tool_results_not_contradicted(Severity.HIGH),
        ]
    )


_default = default_registry()


def register_invariant(invariant: Invariant) -> Invariant:
    """Add an invariant to the module-level registry."""
    return _default.register(invariant)


def invariant(
    name: str,
    description: str = "",
    severity: Severity = Severity.HIGH,
) -> Callable[[Callable[[Trace], list[InvariantViolation]]], Invariant]:
    """Decorator form, for rules that do not fit any builder."""

    def decorate(check: Callable[[Trace], list[InvariantViolation]]) -> Invariant:
        return register_invariant(
            Invariant(name, description or check.__doc__ or name, severity, check)
        )

    return decorate


def run_invariants(
    trace: Trace,
    registry: InvariantRegistry | None = None,
) -> list[InvariantViolation]:
    """Check a trace against a registry, defaulting to the module-level one."""
    return (registry or _default).run(trace)


def validate_invariants(trace: Trace, registry: InvariantRegistry | None = None) -> bool:
    """Whether a trace holds every invariant. The pass/fail form."""
    return not run_invariants(trace, registry)


__all__ = [
    "Invariant",
    "InvariantRegistry",
    "InvariantViolation",
    "default_registry",
    "explain_violation",
    "field_present",
    "field_stable",
    "invariant",
    "numeric_within",
    "observations_of",
    "register_invariant",
    "retrieved_context_relevant",
    "run_invariants",
    "tool_results_not_contradicted",
    "validate_invariants",
]
