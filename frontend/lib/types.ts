/**
 * Types mirroring the backend's Pydantic models.
 *
 * Hand-written rather than generated from the OpenAPI schema. The API surface
 * is small and stable, and a generator would add a build step and a checked-in
 * artifact for types that fit in one readable file. If the surface grows, swap
 * this for generation — that is the point at which it pays for itself.
 */

export type Stage =
  | "preprocessing"
  | "document_load"
  | "chunking"
  | "retrieval"
  | "prompt_build"
  | "llm"
  | "tool"
  | "postprocessing"
  | "validation"
  | "other";

export type SpanStatus = "unset" | "ok" | "error";

export type Severity = "info" | "low" | "medium" | "high" | "critical";

export type Verdict =
  | "healthy"
  | "root_cause_candidate"
  | "downstream_consequence"
  | "unrelated_anomaly";

export type EvidenceKind = "observed" | "rule" | "comparison" | "heuristic";

export interface TraceEvent {
  name: string;
  timestamp: string;
  attributes: Record<string, unknown>;
}

export interface SpanSummary {
  span_id: string;
  parent_span_id: string | null;
  name: string;
  stage: Stage;
  status: SpanStatus;
  start_time: string;
  end_time: string | null;
  duration_ms: number | null;
  error_type: string | null;
  error_message: string | null;
  attributes: Record<string, unknown>;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  events: TraceEvent[];
}

export interface TraceSummary {
  trace_id: string;
  name: string;
  project: string;
  pipeline: string;
  status: SpanStatus;
  start_time: string;
  end_time: string | null;
  duration_ms: number | null;
  span_count: number;
  failed_span_count: number;
  root_cause_stage: Stage | null;
  diagnostic_confidence: number | null;
  analysed: boolean;
}

/**
 * What `GET /traces/{id}` returns: the domain Trace, not the list summary.
 * It carries the full span payloads and the trace attributes, but no derived
 * counts — those are computed where they are displayed.
 */
export interface TraceDetail {
  trace_id: string;
  name: string;
  project: string;
  pipeline: string;
  status: SpanStatus;
  start_time: string;
  end_time: string | null;
  attributes: Record<string, unknown>;
  spans: SpanSummary[];
}

export interface TraceListResponse {
  items: TraceSummary[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface Evidence {
  kind: EvidenceKind;
  description: string;
  span_id: string | null;
  stage: Stage | null;
  detail: Record<string, unknown>;
}

export interface FailureCandidate {
  detector: string;
  category: string;
  severity: Severity;
  confidence: number;
  summary: string;
  span_id: string | null;
  stage: Stage;
  evidence: Evidence[];
}

export interface SpanAssessment {
  span_id: string;
  span_name: string;
  stage: Stage;
  verdict: Verdict;
  candidates: FailureCandidate[];
  depends_on: string[];
  explanation: string;
}

export interface DivergenceReport {
  trace_id: string;
  healthy: boolean;
  first_divergence_span_id: string | null;
  first_divergence_stage: Stage | null;
  assessments: SpanAssessment[];
  explanation: string;
}

export interface RootCauseCandidate {
  rank: number;
  span_id: string;
  span_name: string;
  stage: Stage;
  verdict: Verdict;
  score: number;
  confidence: number;
  summary: string;
  evidence: Evidence[];
  candidates: FailureCandidate[];
  downstream_effects: string[];
  score_components: Record<string, number>;
  explanation: string;
}

export interface RootCauseReport {
  trace_id: string;
  trace_name: string;
  project: string;
  pipeline: string;
  generated_at: string;
  healthy: boolean;
  likely_root_cause: RootCauseCandidate | null;
  ranked_candidates: RootCauseCandidate[];
  first_divergence_span_id: string | null;
  first_divergence_stage: Stage | null;
  evidence_chain: Evidence[];
  downstream_impact: string[];
  recommended_actions: string[];
  summary: string;
  divergence: DivergenceReport;
  analysis_ms: number;
}

export interface Overview {
  total_traces: number;
  failed_traces: number;
  failure_rate: number;
  diagnosed_failure_rate: number;
  root_causes_identified: number;
  average_latency_ms: number;
  top_failure_stages: { stage: string; count: number }[];
  projects: string[];
}

export interface PipelineHealth {
  project: string;
  pipeline: string;
  total_traces: number;
  failed_traces: number;
  failure_rate: number;
  average_latency_ms: number;
}

export interface FailureBreakdownItem {
  category: string;
  stage: string;
  count: number;
}

export interface HealthResponse {
  status: string;
  version: string;
  database: string;
  database_ok: boolean;
}
