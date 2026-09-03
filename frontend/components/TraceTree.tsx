import { duration, payloadPreview, timestamp } from "@/lib/format";
import type { RootCauseReport, SpanSummary, Verdict } from "@/lib/types";

import { DurationCell, StageTag, StatusBadge, VerdictBadge } from "./primitives";

/**
 * The trace tree (Phase 14).
 *
 * Renders spans by parent/child, in execution order, with each stage's status,
 * duration, payloads, errors, and the forensic verdict the engine reached about
 * it. Built from `<details>` so it works before any JavaScript runs and the
 * keyboard behaviour is the browser's rather than a reimplementation.
 *
 * The duration bar is scaled against the longest span in the trace, so the
 * stage that dominated a run is visible without reading any numbers.
 */

interface Props {
  spans: SpanSummary[];
  report: RootCauseReport | null;
  defaultOpenSpanId?: string | null;
}

export function TraceTree({ spans, report, defaultOpenSpanId }: Props) {
  const verdicts = new Map<string, Verdict>(
    (report?.divergence.assessments ?? []).map((a) => [a.span_id, a.verdict]),
  );
  const longest = Math.max(1, ...spans.map((s) => s.duration_ms ?? 0));
  const known = new Set(spans.map((s) => s.span_id));
  const roots = spans.filter((s) => !s.parent_span_id || !known.has(s.parent_span_id));

  return (
    <div className="tree">
      {roots.map((span) => (
        <SpanNode
          key={span.span_id}
          span={span}
          spans={spans}
          verdicts={verdicts}
          longest={longest}
          report={report}
          defaultOpenSpanId={defaultOpenSpanId}
        />
      ))}
    </div>
  );
}

function SpanNode({
  span,
  spans,
  verdicts,
  longest,
  report,
  defaultOpenSpanId,
}: {
  span: SpanSummary;
  spans: SpanSummary[];
  verdicts: Map<string, Verdict>;
  longest: number;
  report: RootCauseReport | null;
  defaultOpenSpanId?: string | null;
}) {
  const children = spans.filter((s) => s.parent_span_id === span.span_id);
  const verdict = verdicts.get(span.span_id);
  const findings =
    report?.divergence.assessments.find((a) => a.span_id === span.span_id)?.candidates ?? [];
  // Open the diverging stage by default: it is what the reader came for.
  const open = defaultOpenSpanId === span.span_id || span.status === "error";
  const share = Math.max(2, ((span.duration_ms ?? 0) / longest) * 100);

  return (
    <div>
      <details className="tree-node" data-verdict={verdict ?? "healthy"} open={open}>
        <summary className="tree-summary">
          <span className="tree-marker" aria-hidden="true" />
          <span className="tree-name">{span.name}</span>
          <StageTag stage={span.stage} />
          <span className="tree-spacer" />
          {findings.length > 0 ? (
            <span className="badge badge-warn">
              {findings.length} finding{findings.length === 1 ? "" : "s"}
            </span>
          ) : null}
          {verdict && verdict !== "healthy" ? <VerdictBadge verdict={verdict} /> : null}
          <StatusBadge status={span.status} />
          <span
            className="duration-bar"
            style={{ width: `${share}px` }}
            aria-hidden="true"
            title={duration(span.duration_ms)}
          />
          <DurationCell ms={span.duration_ms} />
        </summary>

        <div className="tree-body">
          {span.error_type ? (
            <div className="notice notice-error" role="alert">
              <p className="notice-title">
                {span.error_type}
                {span.error_message ? `: ${span.error_message}` : ""}
              </p>
            </div>
          ) : null}

          {findings.length > 0 ? (
            <div>
              <p className="card-title">Detected anomalies</p>
              <ul className="evidence-list">
                {findings.map((finding, index) => (
                  <li className="evidence-item" key={`${finding.detector}-${index}`}>
                    <span className="evidence-index" />
                    <div className="evidence-text">
                      <div>{finding.summary}</div>
                      <div className="evidence-meta">
                        <span className="mono">{finding.detector}</span>
                        <span>confidence {finding.confidence.toFixed(2)}</span>
                        <span>{finding.severity}</span>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <dl className="kv">
            <dt>Span id</dt>
            <dd className="mono">{span.span_id}</dd>
            <dt>Started</dt>
            <dd className="mono">{timestamp(span.start_time)}</dd>
            <dt>Duration</dt>
            <dd className="mono">{duration(span.duration_ms)}</dd>
            {Object.keys(span.attributes).length > 0 ? (
              <>
                <dt>Attributes</dt>
                <dd>
                  <pre className="payload">{payloadPreview(span.attributes, 600)}</pre>
                </dd>
              </>
            ) : null}
            {Object.keys(span.inputs).length > 0 ? (
              <>
                <dt>Inputs</dt>
                <dd>
                  <pre className="payload">{payloadPreview(span.inputs, 1400)}</pre>
                </dd>
              </>
            ) : null}
            {Object.keys(span.outputs).length > 0 ? (
              <>
                <dt>Outputs</dt>
                <dd>
                  <pre className="payload">{payloadPreview(span.outputs, 1400)}</pre>
                </dd>
              </>
            ) : null}
            {span.events.length > 0 ? (
              <>
                <dt>Events</dt>
                <dd>
                  <ul className="action-list">
                    {span.events.map((event, index) => (
                      <li key={`${event.name}-${index}`}>
                        <span className="mono">{event.name}</span>{" "}
                        <span className="faint">{timestamp(event.timestamp)}</span>
                      </li>
                    ))}
                  </ul>
                </dd>
              </>
            ) : null}
          </dl>
        </div>
      </details>

      {children.length > 0 ? (
        <div className="tree-children">
          {children.map((child) => (
            <SpanNode
              key={child.span_id}
              span={child}
              spans={spans}
              verdicts={verdicts}
              longest={longest}
              report={report}
              defaultOpenSpanId={defaultOpenSpanId}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
