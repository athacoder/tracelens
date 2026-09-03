import { duration, percent, stageLabel } from "@/lib/format";
import type { RootCauseReport } from "@/lib/types";

import { ScoreBar, StageTag, Stat } from "./primitives";

/**
 * The forensic report (Phase 15).
 *
 * One screen answering one question: why did this pipeline fail? The layout
 * follows the argument rather than the data model — the verdict first, then the
 * evidence that supports it, then what it cost, then what to do.
 *
 * The evidence list keeps the engine's exculpatory items in place and marks
 * them. "The prompt carried the retrieved content unchanged" is not filler: it
 * is the reason the diagnosis names the retriever and not the model, and
 * hiding it would leave the reader with a claim instead of an argument.
 */
export function ForensicReport({
  report,
  spanNames,
}: {
  report: RootCauseReport;
  spanNames: Map<string, string>;
}) {
  const likely = report.likely_root_cause;

  if (report.healthy || !likely) {
    return (
      <div className="verdict-banner" data-tone="ok">
        <div>
          <p className="verdict-question">Why did this pipeline fail?</p>
          <p className="verdict-answer">It did not.</p>
        </div>
        <p className="muted" style={{ margin: 0 }}>
          {report.divergence.explanation}
        </p>
        <div className="verdict-facts">
          <Stat label="Stages analysed" value={report.divergence.assessments.length} />
          <Stat label="Analysis time" value={duration(report.analysis_ms)} />
        </div>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="verdict-banner" data-tone="error">
        <div>
          <p className="verdict-question">Why did this pipeline fail?</p>
          <p className="verdict-answer">{likely.span_name}</p>
          <p className="muted" style={{ margin: "4px 0 0" }}>
            {likely.summary}
          </p>
        </div>

        <div className="verdict-facts">
          <Stat label="Likely root cause" value={<StageTag stage={likely.stage} />} />
          <Stat
            label="Diagnostic confidence"
            value={percent(likely.confidence)}
            note="Not a calibrated probability"
          />
          <Stat
            label="First divergence"
            value={stageLabel(report.first_divergence_stage)}
            note={likely.span_name}
          />
          <Stat label="Analysis time" value={duration(report.analysis_ms)} />
        </div>
      </div>

      <div className="card">
        <p className="card-title">Evidence</p>
        <ul className="evidence-list">
          {report.evidence_chain.map((item, index) => (
            <li
              className="evidence-item"
              data-role={String(item.detail?.role ?? "cause")}
              key={`${item.description}-${index}`}
            >
              <span className="evidence-index" />
              <div className="evidence-text">
                <div>{item.description}</div>
                <div className="evidence-meta">
                  {item.span_id ? (
                    <a className="link-row" href={`#span-${item.span_id}`}>
                      {spanNames.get(item.span_id) ?? "span"}
                    </a>
                  ) : null}
                  {item.stage ? <StageTag stage={item.stage} /> : null}
                  <span>{item.kind}</span>
                  {item.detail?.role === "exculpatory" ? (
                    <span className="badge badge-ok">clears this stage</span>
                  ) : null}
                  {item.detail?.role === "downstream consequence" ? (
                    <span className="badge badge-warn">consequence</span>
                  ) : null}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div className="card">
        <p className="card-title">Downstream impact</p>
        {report.downstream_impact.length > 0 ? (
          <ul className="action-list">
            {report.downstream_impact.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            No downstream stage reported a further problem. The failure did not propagate into a
            second visible fault, which does not mean the answer was correct.
          </p>
        )}
      </div>

      <div className="card">
        <p className="card-title">Recommended remediation</p>
        <ul className="action-list">
          {report.recommended_actions.map((action) => (
            <li key={action}>{action}</li>
          ))}
        </ul>
      </div>

      <div className="card">
        <p className="card-title">Ranked candidates</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Stage</th>
                <th>Verdict</th>
                <th>Score</th>
                <th>Confidence</th>
                <th>Detectors</th>
              </tr>
            </thead>
            <tbody>
              {report.ranked_candidates.map((candidate) => (
                <tr key={candidate.span_id}>
                  <td className="num mono">{candidate.rank}</td>
                  <td>
                    <a className="link-row" href={`#span-${candidate.span_id}`}>
                      {candidate.span_name}
                    </a>{" "}
                    <StageTag stage={candidate.stage} />
                  </td>
                  <td className="muted">{candidate.explanation}</td>
                  <td>
                    <div className="row">
                      <ScoreBar value={candidate.score} />
                      <span className="mono">{candidate.score.toFixed(2)}</span>
                    </div>
                  </td>
                  <td className="num mono">{candidate.confidence.toFixed(2)}</td>
                  <td className="mono faint">
                    {candidate.candidates
                      .map((c) => c.detector)
                      .filter((value, index, all) => all.indexOf(value) === index)
                      .join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="stat-note" style={{ marginTop: 10 }}>
          Score is severity × confidence × evidence strength, scaled by the stage&apos;s position
          in the causal chain, detector agreement, and downstream impact. It ranks candidates
          within one trace; it is not comparable across traces and is not a probability.
        </p>
      </div>
    </div>
  );
}
