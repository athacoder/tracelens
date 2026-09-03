"""A minimal instrumented RAG pipeline, end to end.

    python examples/rag_pipeline/main.py            # diagnose locally
    python examples/rag_pipeline/main.py --send     # also ship it to the API

Deliberately small and deliberately broken: the retriever returns a superseded
document, every downstream stage behaves perfectly, and the answer is wrong
anyway. That is the failure this project exists to name, and it is the one an
error-rate dashboard cannot see — nothing raises, and the trace's status is ok.

Everything here uses the public SDK. There is no privileged channel to the
forensic engine.
"""

from __future__ import annotations

import argparse
import sys

from tracelens import HttpExporter, MemoryExporter, Stage, Tracer
from tracelens.models import CaptureMode

# -- a two-document "index", one of which has been superseded ---------------

DOCUMENTS = [
    {
        "id": "refund-2026",
        "text": "Customers may return any item within 30 days of delivery for a full refund.",
        "status": "current",
        "effective_date": "2026-01-01",
    },
    {
        "id": "refund-2019",
        "text": "Customers may return any item within 90 days of delivery for a full refund.",
        "status": "outdated",
        "valid_until": "2025-12-31",
        # This field is the only thing in the whole trace that reveals the
        # failure. Recording it is what lets TraceLens find it.
        "superseded_by": "refund-2026",
    },
]

QUESTION = "How many days do customers have to return an item for a refund?"


def retrieve(query: str, *, broken: bool) -> list[dict]:
    """Return the superseded document when broken, the current one otherwise."""
    wanted = "refund-2019" if broken else "refund-2026"
    return [d for d in DOCUMENTS if d["id"] == wanted]


def build_prompt(query: str, documents: list[dict]) -> str:
    context = "\n".join(d["text"] for d in documents)
    return f"Context: {context}\n\nQuestion: {query}"


def generate(prompt: str) -> str:
    """A stand-in model that answers strictly from its context.

    Extractive on purpose: it cannot hallucinate, so anything wrong with the
    answer came from upstream. That is what makes the diagnosis unambiguous.
    """
    context = prompt.split("Question:")[0].removeprefix("Context:").strip()
    return context.split(". ")[0].strip().rstrip(".") + "."


def run(broken: bool, tracer: Tracer) -> str:
    """The instrumented pipeline. Four stages, one trace."""
    with tracer.trace("support-request", example=True):
        with tracer.span("retriever", stage=Stage.RETRIEVAL, inputs={"query": QUESTION}):
            documents = retrieve(QUESTION, broken=broken)
            tracer.set_outputs(documents=documents)

        with tracer.span(
            "prompt-builder",
            stage=Stage.PROMPT_BUILD,
            inputs={"query": QUESTION, "documents": documents},
        ):
            prompt = build_prompt(QUESTION, documents)
            tracer.set_outputs(prompt=prompt)

        with tracer.span(
            "llm",
            stage=Stage.LLM,
            inputs={"prompt": prompt},
            # Section 30: record what produced the output.
            provider="example",
            model="extractive-1",
            prompt_version="v1",
        ):
            answer = generate(prompt)
            tracer.set_outputs(answer=answer)

        with tracer.span("validator", stage=Stage.VALIDATION, inputs={"answer": answer}):
            tracer.set_outputs(answer=answer, non_empty=bool(answer))

    return answer


def diagnose(trace) -> None:
    """Run the forensic engine locally and print the report.

    Imported here rather than at module scope so the tracing half of this
    example runs with only the SDK installed.
    """
    from app.forensics import generate_root_cause_report

    report = generate_root_cause_report(trace)

    print(f"\n  trace status : {trace.status.value}  (nothing raised)")
    if report.healthy:
        print("  diagnosis    : no divergence found\n")
        return

    likely = report.likely_root_cause
    print(f"  root cause   : {likely.span_name} ({likely.stage.value})")
    print(f"  confidence   : {likely.confidence:.0%}  (diagnostic score, not a probability)")
    print(f"  analysed in  : {report.analysis_ms:.1f} ms")
    print("\n  evidence:")
    for index, item in enumerate(report.evidence_chain, start=1):
        role = item.detail.get("role", "cause")
        marker = {"exculpatory": "clears", "downstream consequence": "effect"}.get(role, "cause ")
        print(f"    {index}. [{marker}] {item.description}")
    print("\n  remediation:")
    for action in report.recommended_actions:
        print(f"    - {action}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--healthy", action="store_true", help="run without the injected fault")
    parser.add_argument("--send", action="store_true", help="also export the trace to the API")
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args(argv)

    exporter = HttpExporter(base_url=args.api) if args.send else MemoryExporter()
    tracer = Tracer(
        project="examples",
        pipeline="rag",
        exporter=exporter,
        capture=CaptureMode.FULL,
    )

    answer = run(broken=not args.healthy, tracer=tracer)

    print(f"\n  question : {QUESTION}")
    print(f"  answer   : {answer}")
    print(
        "  expected : Customers may return any item within 30 days of delivery for a full refund."
    )

    if args.send:
        print(f"\n  Exported to {args.api}. Open the dashboard to see the diagnosis.")
        return 0

    diagnose(exporter.last)
    return 0


if __name__ == "__main__":
    sys.exit(main())
