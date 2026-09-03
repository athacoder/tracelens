"""Benchmark CLI (Phases 11 and 12).

    python -m benchmark.run --all
    python -m benchmark.run --scenario outdated_document
    python -m benchmark.run --all --seed 7 --report benchmark/reports/run.txt

Runs the injected pipeline, feeds each trace to the forensic engine exactly as
the API would, and scores the engine's answer against the ground truth
recorded in ``scenarios.py``.

Every run is reproducible: the pipeline has no randomness, and ``--seed``
fixes id generation so two runs of the same command produce byte-identical
traces.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from app.forensics import generate_root_cause_report
from tracelens import MemoryExporter, Tracer, deterministic_ids
from tracelens.models import CaptureMode

from .corpus import QUESTIONS, QUESTIONS_BY_ID, Question
from .metrics import BenchmarkMetrics, CaseResult, format_report, score
from .pipeline import run_pipeline
from .scenarios import SCENARIO_NAMES, Scenario, get_scenario

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


@dataclass
class BenchmarkRun:
    results: list[CaseResult]
    metrics: BenchmarkMetrics
    confusion: dict[str, int]
    traces: list = None  # type: ignore[assignment]


def run_case(question: Question, scenario: Scenario, tracer: Tracer | None = None) -> tuple:
    """Run one question under one scenario and score the diagnosis."""
    outcome = run_pipeline(question, scenario.name, tracer=tracer)
    report = generate_root_cause_report(outcome.trace)

    result = CaseResult(
        scenario=scenario.name,
        question_id=question.id,
        trace_id=outcome.trace.trace_id,
        failure_present=scenario.failure_present,
        expected_stage=scenario.root_stage,
        detected_failure=not report.healthy,
        predicted_stage=report.first_divergence_stage,
        predicted_confidence=report.diagnostic_confidence,
        analysis_ms=report.analysis_ms,
        answer=outcome.answer,
        summary=report.summary,
    )
    return result, outcome.trace


def run_benchmark(
    scenarios: list[str] | None = None,
    questions: list[Question] | None = None,
    seed: int = 0,
    keep_traces: bool = False,
) -> BenchmarkRun:
    """Run the full grid of questions x scenarios."""
    scenario_names = scenarios or SCENARIO_NAMES
    question_list = questions or QUESTIONS

    results: list[CaseResult] = []
    traces: list = []
    confusion: dict[str, int] = {}

    with deterministic_ids(seed):
        for name in scenario_names:
            scenario = get_scenario(name)
            for question in question_list:
                tracer = Tracer(
                    project="benchmark",
                    pipeline="rag",
                    exporter=MemoryExporter(),
                    capture=CaptureMode.FULL,
                )
                result, trace = run_case(question, scenario, tracer)
                results.append(result)
                if keep_traces:
                    traces.append(trace)
                if result.failure_present and not result.localisation_correct:
                    expected = result.expected_stage.value if result.expected_stage else "none"
                    predicted = result.predicted_stage.value if result.predicted_stage else "none"
                    key = f"{result.scenario}: {expected} -> {predicted}"
                    confusion[key] = confusion.get(key, 0) + 1

    return BenchmarkRun(
        results=results,
        metrics=score(results),
        confusion=confusion,
        traces=traces,
    )


def write_reports(run: BenchmarkRun, directory: Path = REPORTS_DIR) -> tuple[Path, Path]:
    """Write the human-readable report and the machine-readable results."""
    directory.mkdir(parents=True, exist_ok=True)
    text_path = directory / "latest.txt"
    json_path = directory / "latest.json"

    text_path.write_text(format_report(run.metrics, run.confusion), encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "metrics": run.metrics.to_dict(),
                "confusion": run.confusion,
                "cases": [r.to_dict() for r in run.results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return text_path, json_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.run",
        description="Run the TraceLens forensic benchmark against known ground truth.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="run every scenario")
    group.add_argument("--scenario", choices=SCENARIO_NAMES, help="run one scenario")
    parser.add_argument("--question", choices=list(QUESTIONS_BY_ID), help="run one question")
    parser.add_argument("--seed", type=int, default=0, help="seed for id generation")
    parser.add_argument("--report", type=Path, help="also write the text report here")
    parser.add_argument("--no-write", action="store_true", help="do not write benchmark/reports/")
    parser.add_argument("--json", action="store_true", help="print metrics as JSON")
    args = parser.parse_args(argv)

    scenarios = None if args.all else [args.scenario]
    questions = [QUESTIONS_BY_ID[args.question]] if args.question else None

    run = run_benchmark(scenarios=scenarios, questions=questions, seed=args.seed)

    if args.json:
        print(json.dumps(run.metrics.to_dict(), indent=2))
    else:
        print(format_report(run.metrics, run.confusion))

    if not args.no_write:
        text_path, json_path = write_reports(run)
        print(f"Wrote {text_path.relative_to(Path.cwd())} and {json_path.name}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(format_report(run.metrics, run.confusion), encoding="utf-8")

    # Non-zero exit when a healthy control was flagged, so CI can gate on it.
    return 1 if run.metrics.false_positives else 0


if __name__ == "__main__":
    sys.exit(main())
