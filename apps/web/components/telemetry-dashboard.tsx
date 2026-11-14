"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  clearTaskTelemetry,
  readTaskTelemetry,
  summarizeTaskTelemetry,
  type TaskContextTelemetry,
} from "../lib/task-telemetry";

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export default function TelemetryDashboard() {
  const [runs, setRuns] = useState<TaskContextTelemetry[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setRuns(readTaskTelemetry());
    setLoaded(true);
  }, []);

  const summary = useMemo(() => summarizeTaskTelemetry(runs), [runs]);

  function clear() {
    clearTaskTelemetry();
    setRuns([]);
  }

  function download() {
    const blob = new Blob([JSON.stringify(runs, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "evidence-layer-task-context-telemetry.json";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section className="workspace-panel" aria-labelledby="telemetry-runs-heading">
      <div className="workspace-section-heading">
        <div>
          <p className="eyebrow">This browser · task-context preparation</p>
          <h2 id="telemetry-runs-heading">Retrieval telemetry</h2>
        </div>
        {runs.length > 0 && (
          <div className="telemetry-actions">
            <button type="button" onClick={download}>Download JSON</button>
            <button type="button" onClick={clear}>Clear</button>
          </div>
        )}
      </div>

      <p className="workspace-note">
        Every successful task-context preparation records its retrieval status, selected-token
        estimate, chunks, latency, relationships, and scoring/fallback on this browser. Task text
        and evidence content are not stored. Agent token usage, provider cost, and answer quality
        are not collected yet.
      </p>

      {loaded && runs.length === 0 && (
        <p className="workspace-empty">
          No task-context runs recorded in this browser.{" "}
          <Link href="/workspace#task-context">Prepare a task context</Link> to start collecting
          retrieval telemetry.
        </p>
      )}

      {runs.length > 0 && (
        <>
          <div className="telemetry-cards" role="status">
            <div className="telemetry-card"><span>Runs</span><strong>{number.format(summary.runs)}</strong></div>
            <div className="telemetry-card"><span>Mean selected tokens</span><strong>{number.format(summary.meanTokens)}</strong></div>
            <div className="telemetry-card"><span>p50 latency</span><strong>{number.format(summary.p50Latency)} ms</strong></div>
            <div className="telemetry-card"><span>p95 latency</span><strong>{number.format(summary.p95Latency)} ms</strong></div>
            <div className="telemetry-card"><span>Relationships</span><strong>{number.format(summary.relationships)}</strong></div>
            <div className="telemetry-card"><span>Scoring fallbacks</span><strong>{number.format(summary.fallbacks)}</strong></div>
          </div>

          <div className="telemetry-table-wrap">
            <table className="telemetry-table">
              <caption>Task-context retrieval runs stored in this browser</caption>
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Status</th>
                  <th scope="col">Selected</th>
                  <th scope="col">Tokens</th>
                  <th scope="col">Chars</th>
                  <th scope="col">Chunks</th>
                  <th scope="col">Links</th>
                  <th scope="col">Latency</th>
                  <th scope="col">Scoring</th>
                </tr>
              </thead>
              <tbody>
                {runs.slice(0, 20).map((run) => (
                  <tr key={run.recordedAt}>
                    <th scope="row">{new Date(run.recordedAt).toLocaleString()}</th>
                    <td>{run.status}</td>
                    <td>{number.format(run.selectedCount)}</td>
                    <td>{number.format(run.usedTokens)}</td>
                    <td>{number.format(run.usedChars)}</td>
                    <td>{number.format(run.chunkCount)}</td>
                    <td>{number.format(run.relationshipCount)}</td>
                    <td>{number.format(run.latencyMs)} ms</td>
                    <td>
                      {run.scoringProvider}
                      {run.fallbackReason ? ` (fallback: ${run.fallbackReason})` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <p className="public-caveat">
        This is browser-local retrieval telemetry, not a server ledger or invoice. It is intended
        to expose the retrieval side while server-persisted task runs and agent-reported cost/quality
        metrics are not yet available.
      </p>
    </section>
  );
}
