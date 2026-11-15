import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TelemetryDashboard from "../components/telemetry-dashboard";
import { ACTIVE_WORKSPACE_STORAGE_KEY } from "../lib/active-workspace";
import { recordTaskTelemetry } from "../lib/task-telemetry";

const FIRST_WORKSPACE = "a1000000-0000-4000-8000-000000000001";
const SECOND_WORKSPACE = "a1000000-0000-4000-8000-000000000002";

function jsonResponse(payload: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

function ledgerRun(workspaceId: string, tokens = 4100) {
  return {
    run_id: `91000000-0000-4000-8000-00000000000${tokens % 10}`,
    workspace_id: workspaceId,
    run_type: "task_context",
    status: "ok",
    created_at: "2026-11-14T20:00:00Z",
    retrieval_latency_ms: 41,
    retrieval_tokens: tokens,
    retrieval_chars: tokens * 4,
    selected_count: 2,
    chunk_count: 3,
    relationship_count: 1,
    scoring_provider: "jev",
    fallback_reason: null,
    use_jev: true,
    agent_provider: null,
    agent_model: null,
    agent_input_tokens: null,
    agent_output_tokens: null,
    agent_total_tokens: null,
    agent_cost_usd: null,
    agent_latency_ms: null,
    quality_status: null,
    quality_score: null,
  };
}

function mockWorkspaceApi(ledger: Record<string, ReturnType<typeof ledgerRun>[]>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/evidence/me")) {
      return jsonResponse({
        workspaces: [
          { id: FIRST_WORKSPACE, name: "Personal" },
          { id: SECOND_WORKSPACE, name: "Second workspace" },
        ],
      });
    }
    if (url.includes(`/workspaces/${SECOND_WORKSPACE}/task-runs`)) {
      return jsonResponse(ledger[SECOND_WORKSPACE] ?? []);
    }
    if (url.includes(`/workspaces/${FIRST_WORKSPACE}/task-runs`)) {
      return jsonResponse(ledger[FIRST_WORKSPACE] ?? []);
    }
    return jsonResponse({}, 404);
  });
}

describe("TelemetryDashboard", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({}) }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an empty state that links back to task-context preparation", () => {
    render(<TelemetryDashboard />);
    expect(screen.getByRole("heading", { name: "Retrieval telemetry" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Prepare a task context" })).toHaveAttribute(
      "href",
      "/workspace#task-context",
    );
  });

  it("renders recorded browser-local run cards and rows", () => {
    recordTaskTelemetry({
      recordedAt: "2026-11-14T00:00:00.000Z",
      status: "ok",
      latencyMs: 250,
      usedTokens: 3200,
      usedChars: 12800,
      selectedCount: 7,
      chunkCount: 264,
      matchedChunkCount: 18,
      relationshipCount: 2,
      scoringProvider: "lexical_stopword_filtered",
      fallbackReason: null,
      topK: 8,
      maxTokens: 3500,
      maxChars: 14000,
      useJev: false,
    });
    render(<TelemetryDashboard />);
    expect(screen.getAllByText("3,200").length).toBeGreaterThan(0);
    expect(screen.getAllByText("250 ms").length).toBeGreaterThan(0);
    expect(screen.getByText("lexical_stopword_filtered")).toBeInTheDocument();
  });

  it("loads the persisted ledger for the saved active workspace", async () => {
    window.localStorage.setItem(ACTIVE_WORKSPACE_STORAGE_KEY, SECOND_WORKSPACE);
    vi.stubGlobal("fetch", mockWorkspaceApi({
      [SECOND_WORKSPACE]: [ledgerRun(SECOND_WORKSPACE)],
    }));
    render(<TelemetryDashboard />);

    expect(await screen.findByText("Server-persisted task runs, newest first")).toBeInTheDocument();
    expect(screen.getAllByText("4,100").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("Workspace")).toHaveValue(SECOND_WORKSPACE);
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining(`/workspaces/${SECOND_WORKSPACE}/task-runs`),
      expect.anything(),
    );
  });

  it("falls back to the first workspace when no active workspace is saved", async () => {
    vi.stubGlobal("fetch", mockWorkspaceApi({
      [FIRST_WORKSPACE]: [ledgerRun(FIRST_WORKSPACE, 2200)],
    }));
    render(<TelemetryDashboard />);

    expect(await screen.findByText("Server-persisted task runs, newest first")).toBeInTheDocument();
    expect(screen.getByLabelText("Workspace")).toHaveValue(FIRST_WORKSPACE);
    expect(window.localStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY)).toBe(FIRST_WORKSPACE);
  });

  it("loads and persists a newly selected workspace", async () => {
    vi.stubGlobal("fetch", mockWorkspaceApi({
      [FIRST_WORKSPACE]: [],
      [SECOND_WORKSPACE]: [ledgerRun(SECOND_WORKSPACE)],
    }));
    render(<TelemetryDashboard />);

    const selector = await screen.findByLabelText("Workspace");
    fireEvent.change(selector, { target: { value: SECOND_WORKSPACE } });

    expect(await screen.findByText("Server-persisted task runs, newest first")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining(`/workspaces/${SECOND_WORKSPACE}/task-runs`),
        expect.anything(),
      );
    });
    expect(selector).toHaveValue(SECOND_WORKSPACE);
    expect(window.localStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY)).toBe(SECOND_WORKSPACE);
  });

  it("reloads the ledger when Refresh is selected", async () => {
    vi.stubGlobal("fetch", mockWorkspaceApi({
      [FIRST_WORKSPACE]: [ledgerRun(FIRST_WORKSPACE)],
    }));
    render(<TelemetryDashboard />);

    expect(await screen.findByText("Server-persisted task runs, newest first")).toBeInTheDocument();
    const calls = () =>
      (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.filter((call) =>
        String(call[0]).includes(`/workspaces/${FIRST_WORKSPACE}/task-runs`),
      );
    await waitFor(() => expect(calls().length).toBe(1));

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(calls().length).toBe(2));
  });
});
