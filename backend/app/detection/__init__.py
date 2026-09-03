"""Failure detection: independent detectors and the pass that runs them all."""

from __future__ import annotations

from dataclasses import dataclass, field

from tracelens.models import Stage, Trace

from .detectors import (
    DEFAULT_STAGE_SCHEMAS,
    StageSchema,
    detect_execution_failure,
    detect_latency_anomaly,
    detect_missing_information,
    detect_retrieval_failure,
    detect_semantic_inconsistency,
    detect_structural_anomaly,
    detect_unsupported_claims,
    validate_schema,
)
from .models import Evidence, EvidenceKind, FailureCandidate, FailureCategory


@dataclass
class DetectionConfig:
    """Per-pipeline tuning. Every field has a defensible default.

    Latency baselines are the one input the engine genuinely cannot derive
    from a single trace, so they are passed in rather than guessed.
    """

    schemas: dict[Stage, StageSchema] = field(default_factory=lambda: dict(DEFAULT_STAGE_SCHEMAS))
    latency_baselines: dict[Stage, float] = field(default_factory=dict)
    relevance_threshold: float = 0.30


def run_detectors(
    trace: Trace,
    config: DetectionConfig | None = None,
) -> list[FailureCandidate]:
    """Run every detector over a trace and collect the findings.

    Order is by span execution time, then by descending weight, so the caller
    reading the raw list sees the earliest problems first — the same ordering
    the first-divergence engine reasons about.
    """
    config = config or DetectionConfig()

    candidates = [
        *detect_execution_failure(trace),
        *validate_schema(trace, config.schemas),
        *detect_missing_information(trace),
        *detect_latency_anomaly(trace, config.latency_baselines),
        *detect_semantic_inconsistency(trace),
        *detect_retrieval_failure(trace, config.relevance_threshold),
        *detect_unsupported_claims(trace),
        *detect_structural_anomaly(trace),
    ]

    position = {span.span_id: i for i, span in enumerate(trace.ordered_spans())}
    # A candidate with no span (a trace-level structural problem) sorts last:
    # it describes the recording, not a step of the pipeline.
    return sorted(
        candidates,
        key=lambda c: (position.get(c.span_id or "", len(position)), -c.weight),
    )


__all__ = [
    "DEFAULT_STAGE_SCHEMAS",
    "DetectionConfig",
    "Evidence",
    "EvidenceKind",
    "FailureCandidate",
    "FailureCategory",
    "StageSchema",
    "detect_execution_failure",
    "detect_latency_anomaly",
    "detect_missing_information",
    "detect_retrieval_failure",
    "detect_semantic_inconsistency",
    "detect_structural_anomaly",
    "detect_unsupported_claims",
    "run_detectors",
    "validate_schema",
]
