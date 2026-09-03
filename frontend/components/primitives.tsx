import type { ReactNode } from "react";

import { duration, percent, stageLabel, verdictLabel } from "@/lib/format";
import type { Severity, SpanStatus, Stage, Verdict } from "@/lib/types";

/** Status carries meaning, so it gets colour and a label, never colour alone. */
export function StatusBadge({ status }: { status: SpanStatus }) {
  const tone = status === "error" ? "badge-error" : status === "ok" ? "badge-ok" : "badge";
  return (
    <span className={`badge ${tone}`}>
      <span className="dot" aria-hidden="true" />
      {status === "unset" ? "unset" : status}
    </span>
  );
}

const VERDICT_TONE: Record<Verdict, string> = {
  healthy: "badge-ok",
  root_cause_candidate: "badge-error",
  downstream_consequence: "badge-warn",
  unrelated_anomaly: "badge-info",
};

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  return <span className={`badge ${VERDICT_TONE[verdict]}`}>{verdictLabel(verdict)}</span>;
}

const SEVERITY_TONE: Record<Severity, string> = {
  info: "badge",
  low: "badge",
  medium: "badge-warn",
  high: "badge-warn",
  critical: "badge-error",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`badge ${SEVERITY_TONE[severity]}`}>{severity}</span>;
}

export function StageTag({ stage }: { stage: Stage | null }) {
  if (!stage) return <span className="faint">—</span>;
  return <span className="stage-tag">{stageLabel(stage)}</span>;
}

export function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {note ? <div className="stat-note">{note}</div> : null}
    </div>
  );
}

/**
 * Empty states tell the reader how to get data, not just that there is none
 * (section 27). An empty dashboard with no next step is a dead end.
 */
export function EmptyState({
  title,
  children,
  command,
}: {
  title: string;
  children?: ReactNode;
  command?: string;
}) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children ? <p>{children}</p> : null}
      {command ? <code>{command}</code> : null}
    </div>
  );
}

export function ErrorNotice({ message }: { message: string }) {
  return (
    <div className="notice notice-error" role="alert">
      <p className="notice-title">Could not load this view</p>
      <p>{message}</p>
    </div>
  );
}

export function ScoreBar({ value }: { value: number }) {
  const width = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div
      className="score-bar"
      role="img"
      aria-label={`Diagnostic score ${percent(value)}`}
      title={percent(value)}
    >
      <div className="score-fill" style={{ width: `${width}%` }} />
    </div>
  );
}

export function DurationCell({ ms }: { ms: number | null }) {
  return <span className="mono">{duration(ms)}</span>;
}

/**
 * Benchmark data is synthetic and must never be mistaken for a production
 * measurement (section 34).
 */
export function SyntheticBanner({ children }: { children: ReactNode }) {
  return <div className="synthetic-banner">{children}</div>;
}
