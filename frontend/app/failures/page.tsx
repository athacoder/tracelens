import Link from "next/link";

import { EmptyState, ErrorNotice, StageTag } from "@/components/primitives";
import { getFailureBreakdown, listTraces } from "@/lib/api";
import { percent, relativeTime, titleCase } from "@/lib/format";
import type { Stage } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata = { title: "Failures · TraceLens" };

export default async function FailuresPage() {
  const [breakdown, diagnosed] = await Promise.all([
    getFailureBreakdown(),
    listTraces({ limit: 40 }),
  ]);

  if (!breakdown.ok) return <ErrorNotice message={breakdown.error} />;

  const total = breakdown.data.reduce((sum, row) => sum + row.count, 0);
  const failing = diagnosed.ok
    ? diagnosed.data.items.filter((trace) => trace.root_cause_stage !== null)
    : [];

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Failures</h1>
        <p className="page-subtitle">
          Every finding the detectors and invariants recorded, grouped by what went wrong and
          where. A single trace usually produces several findings; only one of them is the cause.
        </p>
      </header>

      {total === 0 ? (
        <EmptyState title="No findings recorded" command="python -m benchmark.seed_demo">
          Either nothing has failed, or nothing has been analysed yet.
        </EmptyState>
      ) : (
        <div className="stack">
          <div className="card">
            <p className="card-title">Findings by category and stage</p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Category</th>
                    <th>Stage</th>
                    <th className="num">Findings</th>
                    <th className="num">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {breakdown.data.map((row) => (
                    <tr key={`${row.category}-${row.stage}`}>
                      <td>{titleCase(row.category)}</td>
                      <td>
                        <StageTag stage={row.stage as Stage} />
                      </td>
                      <td className="num mono">{row.count}</td>
                      <td className="num mono">{percent(row.count / total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <p className="card-title">Traces with a diagnosed root cause</p>
            {failing.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>
                No trace currently has a diagnosed root cause.
              </p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Trace</th>
                      <th>Root cause</th>
                      <th className="num">Confidence</th>
                      <th className="num">Started</th>
                    </tr>
                  </thead>
                  <tbody>
                    {failing.map((trace) => (
                      <tr key={trace.trace_id}>
                        <td>
                          <Link className="link-row" href={`/traces/${trace.trace_id}`}>
                            {trace.name}
                          </Link>
                        </td>
                        <td>
                          <StageTag stage={trace.root_cause_stage} />
                        </td>
                        <td className="num mono">{percent(trace.diagnostic_confidence)}</td>
                        <td className="num faint">{relativeTime(trace.start_time)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
