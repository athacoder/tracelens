import type { Severity, Stage, Verdict } from "./types";

/** Milliseconds, rendered at a precision a human can compare at a glance. */
export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1) return `${(ms * 1000).toFixed(0)}µs`;
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 1 : 0)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.floor(ms / 60_000)}m ${((ms % 60_000) / 1000).toFixed(0)}s`;
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function timestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86_400)}d ago`;
}

const STAGE_LABELS: Record<Stage, string> = {
  preprocessing: "Preprocessing",
  document_load: "Document load",
  chunking: "Chunking",
  retrieval: "Retrieval",
  prompt_build: "Prompt build",
  llm: "LLM",
  tool: "Tool",
  postprocessing: "Post-processing",
  validation: "Validation",
  other: "Other",
};

export const stageLabel = (stage: Stage | null | undefined): string =>
  stage ? (STAGE_LABELS[stage] ?? stage) : "—";

const VERDICT_LABELS: Record<Verdict, string> = {
  healthy: "Healthy",
  root_cause_candidate: "Root cause",
  downstream_consequence: "Downstream",
  unrelated_anomaly: "Unrelated",
};

export const verdictLabel = (verdict: Verdict): string => VERDICT_LABELS[verdict] ?? verdict;

export const titleCase = (value: string): string =>
  value.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

/** Ordered lowest to highest, so a caller can compare severities directly. */
export const SEVERITY_ORDER: Severity[] = ["info", "low", "medium", "high", "critical"];

export function truncate(value: string, limit = 160): string {
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}…`;
}

/** Render a payload for display without letting one huge field dominate. */
export function payloadPreview(value: unknown, limit = 400): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return truncate(value, limit);
  try {
    return truncate(JSON.stringify(value, null, 2), limit);
  } catch {
    return String(value);
  }
}

export const shortId = (id: string | null | undefined): string =>
  id ? `${id.slice(0, 8)}…` : "—";
