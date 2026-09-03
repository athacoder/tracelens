"""Populate a TraceLens database with benchmark traces (section 34).

    python -m benchmark.seed_demo
    python -m benchmark.seed_demo --reset --questions 4

Every trace is tagged ``synthetic=True`` and lands in the ``benchmark``
project, so demo data can never be mistaken for a production measurement. The
dashboard reads the same rows the API would have written, because this uses
the same ingest service rather than a special path.
"""

from __future__ import annotations

import argparse
import sys

from app.services.ingest import ingest_trace
from app.storage.database import create_all, drop_all, session_scope
from tracelens import MemoryExporter, Tracer, deterministic_ids
from tracelens.models import CaptureMode

from .corpus import QUESTIONS
from .pipeline import run_pipeline
from .scenarios import SCENARIO_NAMES, get_scenario


def seed(questions: int = 3, scenarios: list[str] | None = None, seed_value: int = 0) -> int:
    """Run the benchmark grid and ingest every trace. Returns the count."""
    names = scenarios or SCENARIO_NAMES
    selected = QUESTIONS[: max(1, questions)]
    ingested = 0

    with deterministic_ids(seed_value), session_scope() as session:
        for name in names:
            scenario = get_scenario(name)
            for question in selected:
                tracer = Tracer(
                    project="benchmark",
                    pipeline="rag",
                    exporter=MemoryExporter(),
                    capture=CaptureMode.FULL,
                )
                outcome = run_pipeline(question, scenario.name, tracer=tracer)
                outcome.trace.attributes["ground_truth_root_stage"] = (
                    scenario.root_stage.value if scenario.root_stage else None
                )
                outcome.trace.attributes["scenario_description"] = scenario.description
                ingest_trace(session, outcome.trace)
                ingested += 1

    return ingested


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.seed_demo",
        description="Fill the TraceLens database with clearly-marked synthetic traces.",
    )
    parser.add_argument(
        "--questions", type=int, default=3, help="questions per scenario (default 3)"
    )
    parser.add_argument("--scenario", action="append", choices=SCENARIO_NAMES)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="drop and recreate every table first (destroys existing traces)",
    )
    args = parser.parse_args(argv)

    if args.reset:
        drop_all()
    create_all()

    count = seed(questions=args.questions, scenarios=args.scenario, seed_value=args.seed)
    print(f"Ingested {count} synthetic traces into project 'benchmark'.")
    print("Every one is tagged synthetic=True. These are not production metrics.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
