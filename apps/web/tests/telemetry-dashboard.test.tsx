import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TelemetryDashboard from "../components/telemetry-dashboard";
import { recordTaskTelemetry } from "../lib/task-telemetry";

describe("TelemetryDashboard", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({}) }));
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

  it("renders recorded run cards and rows", () => {
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
});


  it("loads the persisted server ledger", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          json: async () => ({ workspaces: [{ id: "a1000000-0000-4000-8000-000000000001", name: "Personal" }] }),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          json: async () => [{
            run_id: "91000000-0000-4000-8000-000000000001",
            workspace_id: "a1000000-0000-4000-8000-000000000001",
            run_type: "task_context",
            status: "ok",
            created_at: "2026-11-14T20:00:00Z",
            retrieval_latency_ms: 41,
            retrieval_tokens: 4100,
            retrieval_chars: 16400,
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
          }],
        }),
    );
    render(<TelemetryDashboard />);
    expect(await screen.findByText("Server-persisted task runs, newest first")).toBeInTheDocument();
    expect(screen.getAllByText("4,100").length).toBeGreaterThan(0);
    expect(screen.getByText("Personal")).toBeInTheDocument();
  });
