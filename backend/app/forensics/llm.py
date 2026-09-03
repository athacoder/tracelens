"""LLM provider abstraction for the semantic forensic layer (sections 9 and 29).

Two rules govern everything here.

The model receives **evidence, not the environment**. Its input is a brief
assembled from the deterministic engine's own findings — the trace shape, the
candidates, the invariant results, the expected and actual values. It cannot
read the database, call the API, or see anything the deterministic pass did not
already extract.

The model's answer is **evidence synthesis, not truth**. The deterministic
engine has already decided where the first divergence is. This layer explains
that finding in prose and proposes remediation; when it disagrees, the report
records the disagreement rather than deferring to it.

The default provider is the mock, so the project is fully testable and the
benchmark fully reproducible without a paid API call (section 16).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from .report import RootCauseReport

#: Bumped whenever the brief or the instruction changes, and recorded on every
#: result (section 30). Without it, comparing two analyses months apart means
#: guessing which prompt produced them.
PROMPT_VERSION = "forensic-v1"

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"

INSTRUCTION = """\
You are a forensic analyst for AI pipelines. A deterministic engine has already \
analysed a failed pipeline run and produced the evidence below. Your job is to \
explain that evidence, not to re-investigate it.

Rules:
- Use only the evidence provided. You cannot see the pipeline, the code, or any \
data beyond this brief.
- If the evidence does not support a confident conclusion, say so and lower your \
confidence. An honest "the evidence is ambiguous between the retriever and the \
chunker" is more useful than a confident guess.
- The deterministic engine's first-divergence finding is stated in the brief. If \
you disagree with it, say which stage you would name instead and why.
- Recommend fixes to the pipeline, not to code you cannot see.
"""


class SemanticAnalysis(BaseModel):
    """The structured output the model must produce (section 9)."""

    model_config = ConfigDict(extra="forbid")

    likely_root_cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str
    evidence: list[str] = Field(default_factory=list)
    downstream_impact: list[str] = Field(default_factory=list)
    recommended_fix: list[str] = Field(default_factory=list)


@runtime_checkable
class LLMProvider(Protocol):
    """What the semantic layer needs from a model."""

    name: str
    model: str

    def analyse(self, brief: str) -> SemanticAnalysis: ...


class MockProvider:
    """The default. Restates the deterministic finding and adds nothing.

    This is deliberate rather than a limitation. A stub that invented plausible
    prose would make the semantic layer look like it was working while
    contributing nothing checkable, and would poison the benchmark. Instead it
    paraphrases the evidence it was given and reports a confidence taken
    directly from the deterministic engine, so a run with no API key produces
    output that is honest about its own provenance.
    """

    name = "mock"
    model = "deterministic-restatement"

    def analyse(self, brief: str) -> SemanticAnalysis:
        sections = _parse_brief(brief)
        return SemanticAnalysis(
            likely_root_cause=sections.get("first_divergence", "unknown"),
            confidence=float(sections.get("diagnostic_confidence", 0.0) or 0.0),
            reasoning_summary=(
                "Restated from the deterministic analysis; no language model was "
                "called. " + sections.get("summary", "")
            ).strip(),
            evidence=sections.get("evidence_list", []),
            downstream_impact=sections.get("downstream_list", []),
            recommended_fix=sections.get("remediation_list", []),
        )


class AnthropicProvider:
    """Claude, via the official Anthropic SDK.

    Imported lazily and declared as an optional extra, so neither the default
    install nor CI needs the dependency or a key.
    """

    name = "anthropic"

    def __init__(
        self,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        api_key: str | None = None,
        max_tokens: int = 4096,
        client: object | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._max_tokens = max_tokens
        self._client = client

    def _build_client(self) -> object:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as error:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                "The Anthropic provider needs the optional dependency: pip install 'tracelens[llm]'"
            ) from error
        self._client = (
            anthropic.Anthropic(api_key=self._api_key) if self._api_key else anthropic.Anthropic()
        )
        return self._client

    def analyse(self, brief: str) -> SemanticAnalysis:
        client = self._build_client()
        response = client.messages.parse(  # type: ignore[attr-defined]
            model=self.model,
            max_tokens=self._max_tokens,
            system=INSTRUCTION,
            messages=[{"role": "user", "content": brief}],
            output_format=SemanticAnalysis,
        )
        return response.parsed_output


def get_provider(name: str | None = None) -> LLMProvider:
    """Resolve a provider by name, defaulting to the mock.

    Anything unrecognised falls back to the mock rather than raising: a typo in
    an environment variable should not take the forensic API down, and the
    result records which provider actually ran.
    """
    selected = (name or os.getenv("TRACELENS_LLM_PROVIDER") or "mock").strip().lower()
    if selected == "anthropic":
        return AnthropicProvider()
    return MockProvider()


def build_brief(report: RootCauseReport, max_evidence: int = 12) -> str:
    """Assemble the evidence the model is allowed to see (section 9).

    Deliberately a flat, labelled text block rather than raw JSON: the sections
    are what the mock provider parses back out, which keeps the two providers
    reading the same input.
    """
    likely = report.likely_root_cause
    stage = report.first_divergence_stage.value if report.first_divergence_stage else "none"
    lines = [
        "== trace ==",
        f"name: {report.trace_name}",
        f"project: {report.project}",
        f"pipeline: {report.pipeline}",
        f"healthy: {report.healthy}",
        "",
        "== deterministic finding ==",
        f"first_divergence: {stage}",
        f"first_divergence_span: {likely.span_name if likely else 'none'}",
        f"diagnostic_confidence: {report.diagnostic_confidence}",
        f"summary: {report.summary}",
        "",
        "== stages, in execution order ==",
    ]
    for assessment in report.divergence.assessments:
        lines.append(
            f"- {assessment.span_name} ({assessment.stage.value}): {assessment.verdict.value}"
            + (f" — {assessment.candidates[0].summary}" if assessment.candidates else "")
        )

    lines += ["", "== evidence =="]
    lines += [f"- {item.description}" for item in report.evidence_chain[:max_evidence]]

    lines += ["", "== ranked candidates =="]
    for candidate in report.ranked_candidates[:5]:
        lines.append(
            f"- #{candidate.rank} {candidate.span_name} ({candidate.stage.value}) "
            f"score={candidate.score:.2f} confidence={candidate.confidence:.2f} "
            f"detectors={', '.join(candidate.detectors)}"
        )

    lines += ["", "== downstream impact =="]
    lines += [f"- {item}" for item in report.downstream_impact] or ["- none recorded"]

    lines += ["", "== remediation proposed by the deterministic engine =="]
    lines += [f"- {item}" for item in report.recommended_actions] or ["- none"]

    return "\n".join(lines)


def _parse_brief(brief: str) -> dict:
    """Read a brief back into its parts. Used by the mock provider."""
    sections: dict = {}
    current: str | None = None
    buckets: dict[str, list[str]] = {}

    for raw in brief.splitlines():
        line = raw.strip()
        if line.startswith("== ") and line.endswith(" =="):
            current = line.strip("= ").strip()
            buckets[current] = []
            continue
        if not line:
            continue
        if ": " in line and not line.startswith("- "):
            key, _, value = line.partition(": ")
            sections[key.strip()] = value.strip()
        elif line.startswith("- ") and current:
            buckets[current].append(line[2:].strip())

    sections["evidence_list"] = buckets.get("evidence", [])
    sections["downstream_list"] = [
        item for item in buckets.get("downstream impact", []) if item != "none recorded"
    ]
    sections["remediation_list"] = [
        item
        for item in buckets.get("remediation proposed by the deterministic engine", [])
        if item != "none"
    ]
    return sections
