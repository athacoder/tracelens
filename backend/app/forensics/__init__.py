"""Forensic analysis: dependency inference, first divergence, ranking, reports.

The pipeline is::

    trace -> collect_candidates -> find_first_divergence
          -> rank_failure_candidates -> generate_root_cause_report
"""

from .dependencies import (
    direct_dependencies,
    downstream_from_edges,
    downstream_of,
    shares_data,
    validate_stage_transition,
)
from .divergence import (
    DivergenceReport,
    SpanAssessment,
    Verdict,
    collect_candidates,
    find_first_divergence,
)
from .llm import (
    PROMPT_VERSION,
    AnthropicProvider,
    LLMProvider,
    MockProvider,
    SemanticAnalysis,
    build_brief,
    get_provider,
)
from .report import (
    REMEDIATION,
    RootCauseReport,
    build_evidence_chain,
    generate_root_cause_report,
)
from .scoring import RootCauseCandidate, rank_failure_candidates
from .semantic import SemanticForensicResult, analyse_semantically

__all__ = [
    "PROMPT_VERSION",
    "REMEDIATION",
    "AnthropicProvider",
    "LLMProvider",
    "MockProvider",
    "SemanticAnalysis",
    "SemanticForensicResult",
    "DivergenceReport",
    "RootCauseCandidate",
    "RootCauseReport",
    "SpanAssessment",
    "Verdict",
    "analyse_semantically",
    "build_brief",
    "build_evidence_chain",
    "collect_candidates",
    "direct_dependencies",
    "downstream_from_edges",
    "downstream_of",
    "find_first_divergence",
    "generate_root_cause_report",
    "get_provider",
    "rank_failure_candidates",
    "shares_data",
    "validate_stage_transition",
]
