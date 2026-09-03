"""The semantic forensic layer (Phase 9).

Runs *after* the deterministic engine and consumes its output. The deterministic
finding stays authoritative: this layer explains it, and when the model reaches
a different conclusion the result records the disagreement rather than
overwriting the diagnosis.

That ordering is the whole design. A language model asked to diagnose a trace
from scratch produces a fluent answer whose accuracy nobody can check. Asked to
explain a specific piece of evidence, it produces prose whose claims can be
checked against that evidence — and a disagreement becomes a signal worth
looking at rather than a silent overwrite.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from tracelens.models import Stage, utcnow

from .llm import (
    PROMPT_VERSION,
    LLMProvider,
    SemanticAnalysis,
    build_brief,
    get_provider,
)
from .report import RootCauseReport

logger = logging.getLogger("tracelens.semantic")


class SemanticForensicResult(BaseModel):
    """A model's reading of the evidence, with its provenance attached."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    analysis: SemanticAnalysis

    # Section 30: never rely on a human remembering which model and prompt
    # produced an analysis.
    provider: str
    model: str
    prompt_version: str = PROMPT_VERSION
    generated_at: datetime = Field(default_factory=utcnow)
    latency_ms: float = 0.0

    #: What the deterministic engine concluded, kept beside the model's answer
    #: so the two can be compared without re-running anything.
    deterministic_stage: Stage | None = None
    #: True when the model named a different stage. Not an error: it is the
    #: signal that the evidence is ambiguous and worth a human look.
    disagrees_with_deterministic: bool = False
    error: str | None = None

    @property
    def trustworthy(self) -> bool:
        """Whether this analysis can be quoted without a caveat.

        False when the model failed, or when it disagrees with the
        deterministic engine — in which case the disagreement is the finding.
        """
        return self.error is None and not self.disagrees_with_deterministic


def analyse_semantically(
    report: RootCauseReport,
    provider: LLMProvider | None = None,
) -> SemanticForensicResult:
    """Explain a deterministic report in prose, with structured output.

    A provider failure is captured on the result rather than raised. The
    deterministic diagnosis is complete on its own; losing the narrative layer
    must not lose the answer.
    """
    provider = provider or get_provider()
    brief = build_brief(report)
    started = time.perf_counter()

    try:
        analysis = provider.analyse(brief)
        error = None
    except Exception as failure:  # noqa: BLE001 - any provider fault is recoverable here
        logger.warning("tracelens: semantic analysis failed", exc_info=True)
        analysis = SemanticAnalysis(
            likely_root_cause=(
                report.first_divergence_stage.value if report.first_divergence_stage else "unknown"
            ),
            confidence=0.0,
            reasoning_summary=(
                "The semantic layer could not run; the deterministic diagnosis above "
                "stands on its own."
            ),
        )
        error = f"{type(failure).__name__}: {failure}"

    return SemanticForensicResult(
        trace_id=report.trace_id,
        analysis=analysis,
        provider=provider.name,
        model=provider.model,
        generated_at=utcnow(),
        latency_ms=(time.perf_counter() - started) * 1000.0,
        deterministic_stage=report.first_divergence_stage,
        disagrees_with_deterministic=_disagrees(report, analysis, error),
        error=error,
    )


def _disagrees(
    report: RootCauseReport,
    analysis: SemanticAnalysis,
    error: str | None,
) -> bool:
    """Whether the model named a stage other than the deterministic one.

    Matched loosely on purpose: the model writes prose, so "the retriever
    returned a superseded document" should count as agreeing with `retrieval`.
    Only a clear mention of a *different* known stage, with no mention of the
    deterministic one, is treated as disagreement.
    """
    if error is not None or report.first_divergence_stage is None:
        return False

    claim = analysis.likely_root_cause.lower()
    expected = report.first_divergence_stage
    if _mentions(claim, expected):
        return False
    return any(_mentions(claim, stage) for stage in Stage if stage is not expected)


#: Words a model is likely to use for each stage. Kept explicit rather than
#: matched on the enum value alone, which would miss "the retriever".
STAGE_SYNONYMS: dict[Stage, tuple[str, ...]] = {
    Stage.PREPROCESSING: ("preprocess", "pre-process", "normalis", "normaliz"),
    Stage.DOCUMENT_LOAD: ("document load", "document_load", "loader", "ingest"),
    Stage.CHUNKING: ("chunk",),
    Stage.RETRIEVAL: ("retriev",),
    Stage.PROMPT_BUILD: ("prompt build", "prompt_build", "prompt builder", "prompt construction"),
    Stage.LLM: ("llm", "model generation", "the model", "generation"),
    Stage.TOOL: ("tool", "api call"),
    Stage.POSTPROCESSING: ("postprocess", "post-process", "post processing", "formatter"),
    Stage.VALIDATION: ("validat",),
    Stage.OTHER: (),
}


def _mentions(text: str, stage: Stage) -> bool:
    if stage.value in text:
        return True
    return any(word in text for word in STAGE_SYNONYMS.get(stage, ()))
