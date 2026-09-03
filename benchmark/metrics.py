"""Scoring the forensic engine against ground truth (Phase 12).

Two different questions are measured, and conflating them would flatter the
result:

**Detection** — did TraceLens notice that anything was wrong? Binary, scored
with precision, recall, and F1 over every case including the healthy controls.

**Localisation** — given that something was wrong, did it name the right
stage? Scored only over cases where a failure was actually injected, because
"correctly identified the root cause of a run that had no failure" is not a
thing.

A tool that flags everything scores perfect recall and useless precision. A
tool that flags nothing scores perfect precision and no recall. Reporting both,
plus the false-positive rate on healthy runs, is what makes the number mean
something.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from tracelens.models import Stage


@dataclass
class CaseResult:
    """One question run under one scenario, scored."""

    scenario: str
    question_id: str
    trace_id: str
    failure_present: bool
    expected_stage: Stage | None
    detected_failure: bool
    predicted_stage: Stage | None
    predicted_confidence: float
    analysis_ms: float
    answer: str = ""
    summary: str = ""

    @property
    def detection_correct(self) -> bool:
        return self.detected_failure == self.failure_present

    @property
    def localisation_correct(self) -> bool:
        """Only meaningful where a failure was injected."""
        return self.failure_present and self.predicted_stage == self.expected_stage

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["expected_stage"] = self.expected_stage.value if self.expected_stage else None
        data["predicted_stage"] = self.predicted_stage.value if self.predicted_stage else None
        data["detection_correct"] = self.detection_correct
        data["localisation_correct"] = self.localisation_correct
        return data


@dataclass
class ClassMetrics:
    """Per-failure-class performance, as section 12 requires."""

    scenario: str
    cases: int
    detected: int
    localised: int

    @property
    def detection_rate(self) -> float:
        return self.detected / self.cases if self.cases else 0.0

    @property
    def accuracy(self) -> float:
        return self.localised / self.cases if self.cases else 0.0


@dataclass
class BenchmarkMetrics:
    """The whole run, scored."""

    total_cases: int
    failure_cases: int
    healthy_cases: int

    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int

    root_cause_accuracy: float
    detection_rate: float
    precision: float
    recall: float
    f1: float
    false_positive_rate: float
    mean_analysis_ms: float
    median_analysis_ms: float
    p95_analysis_ms: float
    mean_confidence_when_correct: float
    mean_confidence_when_wrong: float
    per_class: list[ClassMetrics] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["per_class"] = [
            {
                "scenario": c.scenario,
                "cases": c.cases,
                "detected": c.detected,
                "localised": c.localised,
                "detection_rate": round(c.detection_rate, 4),
                "accuracy": round(c.accuracy, 4),
            }
            for c in self.per_class
        ]
        return data


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _mean(values: list[float]) -> float:
    return round(statistics.mean(values), 4) if values else 0.0


def score(results: list[CaseResult]) -> BenchmarkMetrics:
    """Turn a list of scored cases into the metrics section 12 asks for."""
    failures = [r for r in results if r.failure_present]
    healthy = [r for r in results if not r.failure_present]

    true_positives = sum(1 for r in failures if r.detected_failure)
    false_negatives = len(failures) - true_positives
    false_positives = sum(1 for r in healthy if r.detected_failure)
    true_negatives = len(healthy) - false_positives

    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, true_positives + false_negatives)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0

    localised = [r for r in failures if r.localisation_correct]
    latencies = sorted(r.analysis_ms for r in results)

    return BenchmarkMetrics(
        total_cases=len(results),
        failure_cases=len(failures),
        healthy_cases=len(healthy),
        true_positives=true_positives,
        false_positives=false_positives,
        true_negatives=true_negatives,
        false_negatives=false_negatives,
        root_cause_accuracy=_ratio(len(localised), len(failures)),
        detection_rate=recall,
        precision=precision,
        recall=recall,
        f1=f1,
        false_positive_rate=_ratio(false_positives, len(healthy)),
        mean_analysis_ms=_mean([r.analysis_ms for r in results]),
        median_analysis_ms=round(statistics.median(latencies), 4) if latencies else 0.0,
        p95_analysis_ms=round(_percentile(latencies, 0.95), 4) if latencies else 0.0,
        # If the engine is confident exactly when it is right, the confidence
        # number is worth reading. If not, the report should say so.
        mean_confidence_when_correct=_mean([r.predicted_confidence for r in localised]),
        mean_confidence_when_wrong=_mean(
            [r.predicted_confidence for r in failures if not r.localisation_correct]
        ),
        per_class=_per_class(results),
    )


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


def _per_class(results: list[CaseResult]) -> list[ClassMetrics]:
    by_scenario: dict[str, list[CaseResult]] = {}
    for result in results:
        by_scenario.setdefault(result.scenario, []).append(result)

    metrics = []
    for scenario, cases in by_scenario.items():
        if cases[0].failure_present:
            detected = sum(1 for c in cases if c.detected_failure)
            localised = sum(1 for c in cases if c.localisation_correct)
        else:
            # For the healthy class, "detected" means correctly left alone.
            detected = sum(1 for c in cases if not c.detected_failure)
            localised = detected
        metrics.append(ClassMetrics(scenario, len(cases), detected, localised))
    return metrics


def format_report(metrics: BenchmarkMetrics, confusion: dict[str, int] | None = None) -> str:
    """The plain-text report written to benchmark/reports/."""
    lines = [
        "TraceLens forensic benchmark",
        "=" * 62,
        "",
        f"Cases                     {metrics.total_cases}"
        f"  ({metrics.failure_cases} injected, {metrics.healthy_cases} healthy controls)",
        "",
        "Detection (did it notice anything was wrong?)",
        "-" * 62,
        f"  Precision               {metrics.precision:.1%}",
        f"  Recall / detection rate {metrics.recall:.1%}",
        f"  F1                      {metrics.f1:.3f}",
        f"  False-positive rate     {metrics.false_positive_rate:.1%}"
        f"  ({metrics.false_positives}/{metrics.healthy_cases} healthy runs flagged)",
        "",
        "Localisation (did it name the right stage?)",
        "-" * 62,
        f"  Root-cause accuracy     {metrics.root_cause_accuracy:.1%}"
        f"  (over the {metrics.failure_cases} injected failures)",
        f"  Mean confidence, right  {metrics.mean_confidence_when_correct:.2f}",
        f"  Mean confidence, wrong  {metrics.mean_confidence_when_wrong:.2f}",
        "",
        "Analysis latency",
        "-" * 62,
        f"  Mean                    {metrics.mean_analysis_ms:.2f} ms",
        f"  Median                  {metrics.median_analysis_ms:.2f} ms",
        f"  p95                     {metrics.p95_analysis_ms:.2f} ms",
        "",
        "Per failure class",
        "-" * 62,
        f"  {'Scenario':<40}{'Cases':>7}{'Detected':>10}{'Accuracy':>10}",
    ]
    for item in metrics.per_class:
        lines.append(
            f"  {item.scenario:<40}{item.cases:>7}{item.detection_rate:>9.0%}{item.accuracy:>10.0%}"
        )

    if confusion:
        lines += ["", "Misattributions (expected -> predicted)", "-" * 62]
        lines += [f"  {key:<50}{count:>4}" for key, count in sorted(confusion.items())]

    lines += [
        "",
        "Notes",
        "-" * 62,
        "  Synthetic data. Generated by benchmark/pipeline.py against the",
        "  hand-written corpus in benchmark/corpus.py, using a deterministic",
        "  extractive stand-in for the model. Reproduce with:",
        "      python -m benchmark.run --all",
        "",
        "  Read these numbers as a regression suite, not as evidence of",
        "  real-world accuracy. The benchmark, the injections, and the engine",
        "  were written by the same author, the corpus is 8 documents, and",
        "  each injection breaks a stage in a way a detector was built to see.",
        "  A high score here means the engine still separates the cases it was",
        "  designed to separate; it is not a claim about production pipelines,",
        "  paraphrased failures, or faults nobody thought of.",
        "",
        "  Confidence is a diagnostic score, not a calibrated probability.",
        "",
    ]
    return "\n".join(lines)
