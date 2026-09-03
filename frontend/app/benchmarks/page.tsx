import { readFile } from "node:fs/promises";
import path from "node:path";

import { EmptyState, Stat, SyntheticBanner } from "@/components/primitives";
import { percent, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Benchmarks · TraceLens" };

/**
 * Reads the report the benchmark actually produced, from the repository.
 *
 * Deliberately not hard-coded numbers in JSX: a dashboard that displays
 * benchmark results typed in by hand is a dashboard that will keep displaying
 * them long after they stop being true. If the file is absent, the page says
 * so and gives the command that produces it.
 */
const REPORT_PATH = path.join(process.cwd(), "..", "benchmark", "reports", "latest.json");

interface ClassMetrics {
  scenario: string;
  cases: number;
  detected: number;
  localised: number;
  detection_rate: number;
  accuracy: number;
}

interface Metrics {
  total_cases: number;
  failure_cases: number;
  healthy_cases: number;
  root_cause_accuracy: number;
  detection_rate: number;
  precision: number;
  recall: number;
  f1: number;
  false_positive_rate: number;
  mean_analysis_ms: number;
  median_analysis_ms: number;
  p95_analysis_ms: number;
  mean_confidence_when_correct: number;
  mean_confidence_when_wrong: number;
  per_class: ClassMetrics[];
}

async function loadMetrics(): Promise<Metrics | null> {
  try {
    const raw = await readFile(REPORT_PATH, "utf-8");
    return (JSON.parse(raw) as { metrics: Metrics }).metrics;
  } catch {
    return null;
  }
}

export default async function BenchmarksPage() {
  const metrics = await loadMetrics();

  if (!metrics) {
    return (
      <>
        <header className="page-header">
          <h1 className="page-title">Benchmarks</h1>
        </header>
        <EmptyState title="No benchmark report found" command="python -m benchmark.run --all">
          The dashboard reads <span className="mono">benchmark/reports/latest.json</span> rather
          than displaying numbers written into the page, so a stale result cannot outlive the run
          that produced it.
        </EmptyState>
      </>
    );
  }

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Benchmarks</h1>
        <p className="page-subtitle">
          The forensic engine scored against known ground truth: a deliberately broken RAG
          pipeline where the injected stage is recorded before the engine sees the trace.
        </p>
      </header>

      <SyntheticBanner>
        Synthetic data. Read this as a regression suite, not as evidence of real-world accuracy —
        the benchmark, the injections, and the engine share an author.
      </SyntheticBanner>

      <div className="stack">
        <div className="stat-grid">
          <Stat
            label="Root-cause accuracy"
            value={percent(metrics.root_cause_accuracy, 1)}
            note={`over ${metrics.failure_cases} injected failures`}
          />
          <Stat
            label="Detection F1"
            value={metrics.f1.toFixed(3)}
            note={`precision ${percent(metrics.precision)}, recall ${percent(metrics.recall)}`}
          />
          <Stat
            label="False-positive rate"
            value={percent(metrics.false_positive_rate, 1)}
            note={`${metrics.healthy_cases} healthy controls`}
          />
          <Stat
            label="Analysis latency"
            value={`${metrics.mean_analysis_ms.toFixed(1)} ms`}
            note={`median ${metrics.median_analysis_ms.toFixed(1)} ms, p95 ${metrics.p95_analysis_ms.toFixed(1)} ms`}
          />
        </div>

        <div className="card">
          <p className="card-title">Per failure class</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Scenario</th>
                  <th className="num">Cases</th>
                  <th className="num">Detected</th>
                  <th className="num">Localised</th>
                  <th className="num">Accuracy</th>
                </tr>
              </thead>
              <tbody>
                {metrics.per_class.map((row) => (
                  <tr key={row.scenario}>
                    <td>{titleCase(row.scenario)}</td>
                    <td className="num mono">{row.cases}</td>
                    <td className="num mono">{percent(row.detection_rate)}</td>
                    <td className="num mono">{row.localised}</td>
                    <td className="num mono">{percent(row.accuracy)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <p className="card-title">How to read these numbers</p>
          <ul className="action-list">
            <li>
              <strong>Detection</strong> asks whether anything was noticed, scored over every case
              including the healthy controls. A tool that flags everything scores perfect recall
              and useless precision, so both are reported.
            </li>
            <li>
              <strong>Localisation</strong> asks whether the right stage was named, scored only
              over cases where a failure was injected. Averaging the two would flatter the result.
            </li>
            <li>
              Confidence when correct is{" "}
              <span className="mono">{metrics.mean_confidence_when_correct.toFixed(2)}</span> and
              when wrong{" "}
              <span className="mono">{metrics.mean_confidence_when_wrong.toFixed(2)}</span>. It is
              a diagnostic score, not a calibrated probability.
            </li>
            <li>
              Reproduce with <span className="mono">python -m benchmark.run --all</span>. Every
              run is deterministic.
            </li>
          </ul>
        </div>
      </div>
    </>
  );
}
