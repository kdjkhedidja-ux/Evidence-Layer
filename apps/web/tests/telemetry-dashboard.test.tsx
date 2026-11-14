import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import TelemetryDashboard from "../components/telemetry-dashboard";
import { recordTaskTelemetry } from "../lib/task-telemetry";

describe("TelemetryDashboard", () => {
  beforeEach(() => {
    window.localStorage.clear();
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
