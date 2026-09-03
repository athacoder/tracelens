"""Failure scenarios and their ground truth.

Each scenario names exactly one stage where the pipeline was deliberately
broken. That is what makes the benchmark a measurement rather than a demo: the
engine's answer can be compared against a fact, not against a judgement.

The ground truth records what the injection did, never what TraceLens is
expected to say about it. Writing the expected diagnosis into the fixture would
make the benchmark grade itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.detection.models import FailureCategory
from tracelens.models import Stage


@dataclass(frozen=True)
class GroundTruth:
    """What was actually done to the pipeline."""

    failure_present: bool
    root_stage: Stage | None
    failure_type: FailureCategory | None
    expected_behavior: str


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    ground_truth: GroundTruth

    @property
    def root_stage(self) -> Stage | None:
        return self.ground_truth.root_stage

    @property
    def failure_present(self) -> bool:
        return self.ground_truth.failure_present


SCENARIOS: list[Scenario] = [
    Scenario(
        name="healthy",
        description="Nothing is injected. The control case.",
        ground_truth=GroundTruth(
            failure_present=False,
            root_stage=None,
            failure_type=None,
            expected_behavior="The pipeline answers correctly from the current document.",
        ),
    ),
    Scenario(
        name="wrong_document",
        description=(
            "The retriever returns a real, current document that answers a different question."
        ),
        ground_truth=GroundTruth(
            failure_present=True,
            root_stage=Stage.RETRIEVAL,
            failure_type=FailureCategory.RETRIEVAL_FAILURE,
            expected_behavior="The retriever should return the document that answers the question.",
        ),
    ),
    Scenario(
        name="outdated_document",
        description=(
            "The retriever returns the superseded version of the right document. Nothing "
            "downstream can tell it is wrong except the document's own metadata."
        ),
        ground_truth=GroundTruth(
            failure_present=True,
            root_stage=Stage.RETRIEVAL,
            failure_type=FailureCategory.RETRIEVAL_FAILURE,
            expected_behavior="The retriever should exclude documents that have been superseded.",
        ),
    ),
    Scenario(
        name="missing_context",
        description="The retriever returns nothing at all.",
        ground_truth=GroundTruth(
            failure_present=True,
            root_stage=Stage.RETRIEVAL,
            failure_type=FailureCategory.RETRIEVAL_FAILURE,
            expected_behavior="The retriever should return at least one relevant document.",
        ),
    ),
    Scenario(
        name="context_corruption",
        description="The chunker alters a number while splitting the document.",
        ground_truth=GroundTruth(
            failure_present=True,
            root_stage=Stage.CHUNKING,
            failure_type=FailureCategory.SEMANTIC_INCONSISTENCY,
            expected_behavior="Chunking should split text without altering its content.",
        ),
    ),
    Scenario(
        name="prompt_corruption",
        description="The prompt builder drops the retrieved context and sends the question alone.",
        ground_truth=GroundTruth(
            failure_present=True,
            root_stage=Stage.PROMPT_BUILD,
            failure_type=FailureCategory.MISSING_INFORMATION,
            expected_behavior="The prompt should carry the retrieved context to the model.",
        ),
    ),
    Scenario(
        name="schema_violation",
        description="The retriever emits documents as a string instead of a list.",
        ground_truth=GroundTruth(
            failure_present=True,
            root_stage=Stage.RETRIEVAL,
            failure_type=FailureCategory.SCHEMA_VIOLATION,
            expected_behavior="The retriever should emit documents as a list.",
        ),
    ),
    Scenario(
        name="tool_timeout",
        description="The structured-extraction tool call times out.",
        ground_truth=GroundTruth(
            failure_present=True,
            root_stage=Stage.TOOL,
            failure_type=FailureCategory.EXECUTION_ERROR,
            expected_behavior=(
                "The tool should return within its timeout, or the pipeline should degrade."
            ),
        ),
    ),
    Scenario(
        name="wrong_tool_response",
        description="The tool returns a value that contradicts the document it was given.",
        ground_truth=GroundTruth(
            failure_present=True,
            root_stage=Stage.TOOL,
            failure_type=FailureCategory.SEMANTIC_INCONSISTENCY,
            expected_behavior="The tool should extract the value present in its input.",
        ),
    ),
    Scenario(
        name="unsupported_claim",
        description="The model asserts a number that appears nowhere in its prompt.",
        ground_truth=GroundTruth(
            failure_present=True,
            root_stage=Stage.LLM,
            failure_type=FailureCategory.SEMANTIC_INCONSISTENCY,
            expected_behavior="The model should answer only from the context it was given.",
        ),
    ),
    Scenario(
        name="postprocessing_corruption",
        description="The post-processor rewrites a number in an otherwise correct answer.",
        ground_truth=GroundTruth(
            failure_present=True,
            root_stage=Stage.POSTPROCESSING,
            failure_type=FailureCategory.SEMANTIC_INCONSISTENCY,
            expected_behavior=(
                "Post-processing should format the answer without changing its values."
            ),
        ),
    ),
    # -- harder tier ------------------------------------------------------
    # The ten scenarios above each break exactly one stage, which makes them a
    # fair test of the detectors but a weak test of the discrimination logic
    # that is the point of the project. These three add a competing signal, so
    # that getting the answer right requires ranking and dependency reasoning
    # rather than just noticing that something is wrong.
    Scenario(
        name="stale_retrieval_with_slow_model",
        description=(
            "The retriever returns the superseded document and the model is also slow. "
            "Latency is the louder signal and the wrong answer."
        ),
        ground_truth=GroundTruth(
            failure_present=True,
            root_stage=Stage.RETRIEVAL,
            failure_type=FailureCategory.RETRIEVAL_FAILURE,
            expected_behavior=(
                "The retriever should exclude superseded documents. The model being slow "
                "is a separate, lesser problem."
            ),
        ),
    ),
    Scenario(
        name="compound_retrieval_and_postprocessing",
        description=(
            "Two real faults in one run: the retriever returns the superseded document "
            "and the post-processor also corrupts a number. The first divergence is the "
            "retriever."
        ),
        ground_truth=GroundTruth(
            failure_present=True,
            root_stage=Stage.RETRIEVAL,
            failure_type=FailureCategory.RETRIEVAL_FAILURE,
            expected_behavior=(
                "The earliest fault should be named as the root cause, with the later one "
                "reported separately rather than replacing it."
            ),
        ),
    ),
    Scenario(
        name="slow_but_correct",
        description=(
            "The document loader is slow. Nothing is wrong with the answer. A tool that "
            "confuses slow with broken fails here."
        ),
        ground_truth=GroundTruth(
            failure_present=False,
            root_stage=None,
            failure_type=None,
            expected_behavior=(
                "Latency alone is not a divergence; the run should read as healthy."
            ),
        ),
    ),
]

#: Scenarios that inject a competing signal rather than a single clean fault.
HARD_SCENARIOS = frozenset(
    {
        "stale_retrieval_with_slow_model",
        "compound_retrieval_and_postprocessing",
        "slow_but_correct",
    }
)

SCENARIOS_BY_NAME = {scenario.name: scenario for scenario in SCENARIOS}
SCENARIO_NAMES = [scenario.name for scenario in SCENARIOS]
FAILURE_SCENARIOS = [s for s in SCENARIOS if s.failure_present]


def get_scenario(name: str) -> Scenario:
    if name not in SCENARIOS_BY_NAME:
        raise KeyError(f"unknown scenario {name!r}; known: {', '.join(SCENARIO_NAMES)}")
    return SCENARIOS_BY_NAME[name]
