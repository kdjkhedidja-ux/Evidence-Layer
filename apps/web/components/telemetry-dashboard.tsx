"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { readActiveWorkspaceId, writeActiveWorkspaceId } from "../lib/active-workspace";
import {
  clearTaskTelemetry,
  readTaskTelemetry,
  summarizeTaskTelemetry,
  type TaskContextTelemetry,
} from "../lib/task-telemetry";
import type { TaskRunLedgerItem } from "../lib/evidence-api";

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type WorkspaceOption = { id: string; name: string };

export default function TelemetryDashboard() {
  const [runs, setRuns] = useState<TaskContextTelemetry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [workspaces, setWorkspaces] = useState<WorkspaceOption[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [directoryState, setDirectoryState] = useState<"loading" | "ready" | "unavailable">("loading");
  const [serverRuns, setServerRuns] = useState<TaskRunLedgerItem[]>([]);
  const [serverState, setServerState] = useState<"loading" | "ready" | "unavailable">("loading");

  useEffect(() => {
    setRuns(readTaskTelemetry());
    setLoaded(true);
  }, []);

  const loadServerLedger = useCallback(async (id: string) => {
    if (!id) {
      setServerRuns([]);
      setServerState("unavailable");
      return;
    }
    setServerRuns([]);
    setServerState("loading");
    try {
      const response = await fetch(
        `/api/evidence/workspaces/${encodeURIComponent(id)}/task-runs`,
        { cache: "no-store" },
      );
      if (response.status === 404) {
        setServerState("unavailable");
        return;
      }
      if (!response.ok) throw new Error("server ledger unavailable");
      const payload = await response.json();
      if (!Array.isArray(payload)) throw new Error("invalid server ledger");
      setServerRuns(payload);
      setServerState("ready");
    } catch {
      setServerState("unavailable");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadWorkspaceDirectory() {
      try {
        const me = await fetch("/api/evidence/me", { cache: "no-store" });
        if (!me.ok) throw new Error("workspace directory unavailable");
        const directory = await me.json();
        const options: WorkspaceOption[] = Array.isArray(directory.workspaces)
          ? directory.workspaces.filter((item: unknown): item is WorkspaceOption => {
              if (!item || typeof item !== "object") return false;
              const candidate = item as Record<string, unknown>;
              return (
                typeof candidate.id === "string" &&
                UUID.test(candidate.id) &&
                typeof candidate.name === "string"
              );
            })
          : [];
        if (cancelled) return;
        setWorkspaces(options);
        if (options.length === 0) {
          setDirectoryState("unavailable");
          setServerState("unavailable");
          return;
        }
        const requested = new URLSearchParams(window.location.search).get("workspace");
        const saved = readActiveWorkspaceId();
        const next =
          options.find((workspace) => workspace.id === requested)?.id ??
          options.find((workspace) => workspace.id === saved)?.id ??
          options[0]?.id ??
          "";
        setWorkspaceId(next);
        if (next) writeActiveWorkspaceId(next);
        setDirectoryState("ready");
      } catch {
        if (!cancelled) {
          setDirectoryState("unavailable");
          setServerState("unavailable");
        }
      }
    }
    void loadWorkspaceDirectory();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!workspaceId) return;
    void loadServerLedger(workspaceId);
  }, [workspaceId, loadServerLedger]);

  useEffect(() => {
    function onFocus() {
      if (workspaceId) void loadServerLedger(workspaceId);
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [workspaceId, loadServerLedger]);

  const selectedWorkspace = workspaces.find((workspace) => workspace.id === workspaceId) ?? null;

  const serverSummary = useMemo(() => {
    const latency = serverRuns.map((run) => run.retrieval_latency_ms).sort((a, b) => a - b);
    return {
      runs: serverRuns.length,
      meanTokens: serverRuns.length
        ? serverRuns.reduce((sum, run) => sum + run.retrieval_tokens, 0) / serverRuns.length
        : 0,
      p50: latency.length ? latency[Math.max(0, Math.ceil(latency.length * 0.5) - 1)] : 0,
      p95: latency.length ? latency[Math.max(0, Math.ceil(latency.length * 0.95) - 1)] : 0,
      relationships: serverRuns.reduce((sum, run) => sum + run.relationship_count, 0),
      fallbacks: serverRuns.filter((run) => Boolean(run.fallback_reason)).length,
    };
  }, [serverRuns]);

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
    <>
    <section className="workspace-panel" aria-labelledby="server-telemetry-heading">
      <div className="workspace-section-heading">
        <div>
          <p className="eyebrow">Server ledger · workspace</p>
          <h2 id="server-telemetry-heading">Persisted task runs</h2>
        </div>
        {selectedWorkspace && <span className="workspace-role">{selectedWorkspace.name}</span>}
      </div>
      <div className="workspace-selector">
        <label htmlFor="telemetry-workspace">Workspace</label>
        <select
          id="telemetry-workspace"
          value={workspaceId}
          onChange={(event) => {
            setWorkspaceId(event.target.value);
            writeActiveWorkspaceId(event.target.value);
          }}
          disabled={directoryState !== "ready"}
        >
          {workspaces.map((workspace) => (
            <option key={workspace.id} value={workspace.id}>{workspace.name}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => {
            if (workspaceId) void loadServerLedger(workspaceId);
          }}
          disabled={!workspaceId || serverState === "loading"}
        >
          Refresh
        </button>
      </div>
      <p className="workspace-note">
        Content-free task-context runs stored by the Evidence Service for the selected workspace.
        Only successful task-context preparations write here; retrieve/API-key, Remote MCP, and
        agent runs are not persisted. The ledger keeps status, retrieval latency, selected
        tokens/chars, chunks, relationships, scoring provider, and fallback metadata. It does not
        store task text, evidence content, answer text, or agent cost.
      </p>
      {directoryState === "loading" && <p className="workspace-note" role="status">Loading authorized workspaces…</p>}
      {directoryState === "unavailable" && (
        <p className="workspace-note">
          No authorized workspace is available, so the server ledger cannot be selected.
        </p>
      )}
      {serverState === "loading" && <p className="workspace-note" role="status">Loading the server ledger…</p>}
      {serverState === "unavailable" && (
        <p className="workspace-note">
          The server ledger is not available for this workspace yet. The browser-local telemetry
          below is still recorded.
        </p>
      )}
      {serverState === "ready" && serverRuns.length === 0 && (
        <p className="workspace-empty">No persisted task-context runs for this workspace yet.</p>
      )}
      {serverState === "ready" && serverRuns.length > 0 && (
        <>
          <div className="telemetry-cards" role="status">
            <div className="telemetry-card"><span>Runs</span><strong>{number.format(serverSummary.runs)}</strong></div>
            <div className="telemetry-card"><span>Mean selected tokens</span><strong>{number.format(serverSummary.meanTokens)}</strong></div>
            <div className="telemetry-card"><span>p50 latency</span><strong>{number.format(serverSummary.p50)} ms</strong></div>
            <div className="telemetry-card"><span>p95 latency</span><strong>{number.format(serverSummary.p95)} ms</strong></div>
            <div className="telemetry-card"><span>Relationships</span><strong>{number.format(serverSummary.relationships)}</strong></div>
            <div className="telemetry-card"><span>Scoring fallbacks</span><strong>{number.format(serverSummary.fallbacks)}</strong></div>
          </div>
          <div className="telemetry-table-wrap">
            <table className="telemetry-table">
              <caption>Server-persisted task runs, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Status</th>
                  <th scope="col">Selected</th>
                  <th scope="col">Tokens</th>
                  <th scope="col">Chunks</th>
                  <th scope="col">Links</th>
                  <th scope="col">Latency</th>
                  <th scope="col">Scoring</th>
                </tr>
              </thead>
              <tbody>
                {serverRuns.slice(0, 20).map((run) => (
                  <tr key={run.run_id}>
                    <th scope="row">{new Date(run.created_at).toLocaleString()}</th>
                    <td>{run.status}</td>
                    <td>{number.format(run.selected_count)}</td>
                    <td>{number.format(run.retrieval_tokens)}</td>
                    <td>{number.format(run.chunk_count)}</td>
                    <td>{number.format(run.relationship_count)}</td>
                    <td>{number.format(run.retrieval_latency_ms)} ms</td>
                    <td>
                      {run.scoring_provider}
                      {run.fallback_reason ? ` (fallback: ${run.fallback_reason})` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>

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
        This lower section is still browser-local and remains useful when the server ledger is
        unavailable. The server ledger above is the persisted workspace view; neither section is an
        invoice or a measure of final answer quality.
      </p>
    </section>
    </>
  );
}
