import Link from "next/link";

import { DurationCell, EmptyState, ErrorNotice, StageTag, Stat } from "@/components/primitives";
import { getOverview, getPipelineHealth, listTraces } from "@/lib/api";
import { duration, percent, relativeTime } from "@/lib/format";
import type { Stage } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const [overview, pipelines, recent] = await Promise.all([
    getOverview(),
    getPipelineHealth(),
    listTraces({ limit: 8 }),
  ]);

  if (!overview.ok) return <ErrorNotice message={overview.error} />;

  const stats = overview.data;

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Overview</h1>
        <p className="page-subtitle">
          Where pipelines are diverging, across every project TraceLens has ingested.
        </p>
      </header>

      {stats.total_traces === 0 ? (
        <EmptyState
          title="No traces yet"
          command="python -m benchmark.seed_demo --questions 3"
        >
          Instrument a pipeline with the SDK, or fill the database with clearly-marked synthetic
          traces from the benchmark.
        </EmptyState>
      ) : (
        <div className="stack">
          <div className="stat-grid">
            <Stat label="Traces" value={stats.total_traces} />
            <Stat
              label="Diagnosed failures"
              value={percent(stats.diagnosed_failure_rate, 1)}
              note={`${stats.root_causes_identified} root causes identified`}
            />
            <Stat
              label="Execution failures"
              value={percent(stats.failure_rate, 1)}
              note={`${stats.failed_traces} traces raised an error`}
            />
            <Stat label="Average latency" value={duration(stats.average_latency_ms)} />
          </div>

          {stats.diagnosed_failure_rate > stats.failure_rate ? (
            <div className="notice">
              <p className="notice-title">
                {percent(stats.diagnosed_failure_rate - stats.failure_rate, 1)} of runs failed
                without raising anything
              </p>
              <p className="muted">
                These pipelines reported success. A retriever that returns a superseded document
                raises no exception, so an error-rate dashboard shows them as healthy while the
                user receives a wrong answer. That gap is what TraceLens exists to close.
              </p>
            </div>
          ) : null}

          <div className="card">
            <p className="card-title">Top failure stages</p>
            {stats.top_failure_stages.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>
                No root causes identified yet.
              </p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Stage</th>
                      <th className="num">Traces</th>
                      <th className="num">Share of diagnosed failures</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.top_failure_stages.map((row) => (
                      <tr key={row.stage}>
                        <td>
                          <StageTag stage={row.stage as Stage} />
                        </td>
                        <td className="num mono">{row.count}</td>
                        <td className="num mono">
                          {percent(
                            stats.root_causes_identified
                              ? row.count / stats.root_causes_identified
                              : 0,
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card">
            <p className="card-title">Pipelines</p>
            {!pipelines.ok ? (
              <ErrorNotice message={pipelines.error} />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Project</th>
                      <th>Pipeline</th>
                      <th className="num">Traces</th>
                      <th className="num">Error rate</th>
                      <th className="num">Avg latency</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pipelines.data.map((row) => (
                      <tr key={`${row.project}/${row.pipeline}`}>
                        <td>{row.project}</td>
                        <td className="mono">{row.pipeline}</td>
                        <td className="num mono">{row.total_traces}</td>
                        <td className="num mono">{percent(row.failure_rate, 1)}</td>
                        <td className="num">
                          <DurationCell ms={row.average_latency_ms} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card">
            <div className="spread">
              <p className="card-title">Recent traces</p>
              <Link className="link-row" href="/traces">
                View all →
              </Link>
            </div>
            {!recent.ok ? (
              <ErrorNotice message={recent.error} />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Trace</th>
                      <th>Root cause</th>
                      <th className="num">Duration</th>
                      <th className="num">Started</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.data.items.map((trace) => (
                      <tr key={trace.trace_id}>
                        <td>
                          <Link className="link-row" href={`/traces/${trace.trace_id}`}>
                            {trace.name}
                          </Link>
                        </td>
                        <td>
                          {trace.root_cause_stage ? (
                            <StageTag stage={trace.root_cause_stage} />
                          ) : (
                            <span className="badge badge-ok">healthy</span>
                          )}
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
            )}
          </div>
        </div>
      )}
    </>
  );
}

export const metadata = { title: "Overview · TraceLens" };
