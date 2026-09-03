import Link from "next/link";

import {
  DurationCell,
  EmptyState,
  ErrorNotice,
  StageTag,
  StatusBadge,
} from "@/components/primitives";
import { listTraces } from "@/lib/api";
import { percent, relativeTime, shortId } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Traces · TraceLens" };

const PAGE_SIZE = 25;

type Search = Record<string, string | string[] | undefined>;

const one = (value: string | string[] | undefined): string | undefined =>
  Array.isArray(value) ? value[0] : value;

export default async function TracesPage({
  searchParams,
}: {
  searchParams: Promise<Search>;
}) {
  const params = await searchParams;
  const offset = Number(one(params.offset) ?? 0) || 0;
  const project = one(params.project);
  const stage = one(params.stage);
  const failedOnly = one(params.failed) === "1";

  const result = await listTraces({
    limit: PAGE_SIZE,
    offset,
    project,
    stage,
    failed_only: failedOnly || undefined,
  });

  if (!result.ok) return <ErrorNotice message={result.error} />;

  const { items, total, has_more: hasMore } = result.data;
  const filtered = Boolean(project || stage || failedOnly);
  const query = (next: Record<string, string | undefined>) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries({ project, stage, failed: failedOnly ? "1" : undefined, ...next })) {
      if (value) search.set(key, value);
    }
    const suffix = search.toString();
    return `/traces${suffix ? `?${suffix}` : ""}`;
  };

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Traces</h1>
        <p className="page-subtitle">
          Every recorded pipeline run, newest first. A trace can report status ok and still carry a
          diagnosed failure — that is the column worth reading.
        </p>
      </header>

      <div className="row" style={{ marginBottom: 16 }}>
        <Link className={`badge ${filtered ? "" : "badge-accent"}`} href="/traces">
          All
        </Link>
        <Link
          className={`badge ${failedOnly ? "badge-accent" : ""}`}
          href={query({ failed: failedOnly ? undefined : "1", offset: undefined })}
        >
          Raised an error
        </Link>
        <span className="faint mono">
          {total} trace{total === 1 ? "" : "s"}
          {filtered ? " matching" : ""}
        </span>
      </div>

      {items.length === 0 ? (
        <EmptyState
          title={filtered ? "No traces match this filter" : "No traces yet"}
          command={filtered ? undefined : "python -m benchmark.seed_demo --questions 3"}
        >
          {filtered
            ? "Clear the filter to see every recorded run."
            : "Instrument a pipeline with the SDK, or seed clearly-marked synthetic traces."}
        </EmptyState>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Trace</th>
                  <th>Project</th>
                  <th>Status</th>
                  <th>Diagnosis</th>
                  <th className="num">Confidence</th>
                  <th className="num">Spans</th>
                  <th className="num">Duration</th>
                  <th className="num">Started</th>
                </tr>
              </thead>
              <tbody>
                {items.map((trace) => (
                  <tr key={trace.trace_id}>
                    <td>
                      <Link className="link-row" href={`/traces/${trace.trace_id}`}>
                        {trace.name}
                      </Link>
                      <div className="mono faint">{shortId(trace.trace_id)}</div>
                    </td>
                    <td>
                      <div>{trace.project}</div>
                      <div className="mono faint">{trace.pipeline}</div>
                    </td>
                    <td>
                      <StatusBadge status={trace.status} />
                    </td>
                    <td>
                      {!trace.analysed ? (
                        <span className="badge">not analysed</span>
                      ) : trace.root_cause_stage ? (
                        <StageTag stage={trace.root_cause_stage} />
                      ) : (
                        <span className="badge badge-ok">no divergence</span>
                      )}
                    </td>
                    <td className="num mono">
                      {trace.diagnostic_confidence !== null && trace.root_cause_stage
                        ? percent(trace.diagnostic_confidence)
                        : "—"}
                    </td>
                    <td className="num mono">
                      {trace.span_count}
                      {trace.failed_span_count > 0 ? (
                        <span className="badge badge-error" style={{ marginLeft: 6 }}>
                          {trace.failed_span_count} failed
                        </span>
                      ) : null}
                    </td>
                    <td className="num">
                      <DurationCell ms={trace.duration_ms} />
                    </td>
                    <td className="num faint">{relativeTime(trace.start_time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="row" style={{ marginTop: 14, justifyContent: "space-between" }}>
            <span className="faint mono">
              {offset + 1}–{offset + items.length} of {total}
            </span>
            <span className="row">
              {offset > 0 ? (
                <Link
                  className="badge badge-accent"
                  href={query({ offset: String(Math.max(0, offset - PAGE_SIZE)) })}
                >
                  ← Previous
                </Link>
              ) : null}
              {hasMore ? (
                <Link
                  className="badge badge-accent"
                  href={query({ offset: String(offset + PAGE_SIZE) })}
                >
                  Next →
                </Link>
              ) : null}
            </span>
          </div>
        </>
      )}
    </>
  );
}
