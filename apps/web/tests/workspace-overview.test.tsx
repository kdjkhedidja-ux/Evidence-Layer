import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import WorkspaceOverview from "../app/workspace/workspace-overview";
import type { WorkspaceDashboard } from "../app/workspace/workspace-data";

const DASHBOARD: WorkspaceDashboard = {
  workspace: {
    id: "a1000000-0000-4000-8000-000000000001",
    name: "Personal workspace",
  },
  role: "owner",
  evidence: {
    kind: "ready",
    count: 1,
    records: [{
      id: "e1000000-0000-4000-8000-000000000001",
      content: "Stored source evidence",
      source_label: "Release note",
      created_at: "2026-10-31T12:00:00Z",
    }],
  },
};

describe("basic Workspace page", () => {
  it("shows the real workspace, evidence, current access, and Explorer link", () => {
    render(<WorkspaceOverview result={{ kind: "ready", dashboard: DASHBOARD }} email="owner@example.test" />);

    expect(screen.getByRole("heading", { name: "Personal workspace" })).toBeInTheDocument();
    expect(screen.getByText("Not recorded in the current schema")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "1" })).toBeInTheDocument();
    expect(screen.getByText("Stored source evidence")).toBeInTheDocument();
    expect(screen.getByText("owner@example.test")).toBeInTheDocument();
    expect(screen.getByText("Owner")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Evidence Explorer" })).toHaveAttribute("href", "/");
  });

  it("shows an explicit empty state rather than invented evidence", () => {
    render(
      <WorkspaceOverview
        email="member@example.test"
        result={{
          kind: "ready",
          dashboard: {
            ...DASHBOARD,
            role: "member",
            evidence: { kind: "ready", count: 0, records: [] },
          },
        }}
      />,
    );

    expect(screen.getByText("No evidence in this workspace yet.")).toBeInTheDocument();
    expect(screen.getByText("Member")).toBeInTheDocument();
    expect(screen.queryByText("Stored source evidence")).not.toBeInTheDocument();
  });

  it("distinguishes no membership from unavailable data", () => {
    const { rerender } = render(<WorkspaceOverview email="" result={{ kind: "no-workspace" }} />);
    expect(screen.getByRole("heading", { name: "No active workspace" })).toBeInTheDocument();

    rerender(<WorkspaceOverview email="" result={{ kind: "unavailable" }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("No workspace or evidence data is shown.");
  });

  it("does not show an empty count when evidence could not be loaded", () => {
    render(
      <WorkspaceOverview
        email="member@example.test"
        result={{
          kind: "ready",
          dashboard: {
            ...DASHBOARD,
            evidence: { kind: "unavailable" },
          },
        }}
      />,
    );

    expect(screen.getByRole("heading", { name: "Unavailable" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Evidence records could not be loaded.");
  });
});
