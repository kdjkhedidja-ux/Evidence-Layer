import { describe, expect, it, vi } from "vitest";
import { loadWorkspaceDashboard } from "../app/workspace/workspace-data";

const USER_ID = "10000000-0000-4000-8000-000000000001";
const ORG_ID = "b2000000-0000-4000-8000-000000000002";
const OTHER_ID = "a1000000-0000-4000-8000-000000000001";
const EVIDENCE_ID = "e1000000-0000-4000-8000-000000000001";

function makeClient(results: Record<string, unknown>) {
  const calls: Array<{ table: string; method: string; args: unknown[] }> = [];
  const client = {
    from: vi.fn((table: string) => {
      const builder = {
        select: vi.fn((...args: unknown[]) => {
          calls.push({ table, method: "select", args });
          return builder;
        }),
        eq: vi.fn((...args: unknown[]) => {
          calls.push({ table, method: "eq", args });
          return builder;
        }),
        in: vi.fn(async (...args: unknown[]) => {
          calls.push({ table, method: "in", args });
          return results[table];
        }),
        order: vi.fn((...args: unknown[]) => {
          calls.push({ table, method: "order", args });
          return builder;
        }),
        limit: vi.fn(async (value: number) => {
          calls.push({ table, method: "limit", args: [value] });
          return results[table];
        }),
      };
      return builder;
    }),
  };
  return { client, calls };
}

function membership(data: unknown, error: unknown = null) {
  return { data, error };
}

function workspace(data: unknown, error: unknown = null) {
  return { data, error };
}

function evidence(data: unknown, count: number | null, error: unknown = null) {
  return { data, count, error };
}

describe("workspace dashboard data", () => {
  it("uses the verified user's RLS-scoped memberships and records", async () => {
    const records = Array.from({ length: 5 }, (_, index) => ({
      id: index === 0 ? EVIDENCE_ID : `e2000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
      content: `Stored evidence ${index + 1}`,
      source_label: "Release note",
      created_at: `2026-10-31T12:0${index}:00Z`,
    }));
    const { client, calls } = makeClient({
      workspace_memberships: membership([
        { workspace_id: ORG_ID, role: "member", created_at: "2026-10-01T00:00:00Z" },
        { workspace_id: OTHER_ID, role: "owner", created_at: "2026-10-02T00:00:00Z" },
      ]),
      workspaces: workspace([
        { id: ORG_ID, name: "Research group" },
        { id: OTHER_ID, name: "Personal workspace" },
      ]),
      evidence_records: evidence(records, 7),
    });

    const result = await loadWorkspaceDashboard(client as never, USER_ID);

    expect(result).toEqual({
      kind: "ready",
      dashboard: {
        workspace: { id: ORG_ID, name: "Research group" },
        role: "member",
        evidence: { kind: "ready", count: 7, records },
      },
    });
    expect(calls).toContainEqual({ table: "workspace_memberships", method: "eq", args: ["user_id", USER_ID] });
    expect(calls).toContainEqual({ table: "workspace_memberships", method: "eq", args: ["status", "active"] });
    expect(calls).toContainEqual({ table: "evidence_records", method: "eq", args: ["workspace_id", ORG_ID] });
    expect(calls).toContainEqual({ table: "evidence_records", method: "limit", args: [5] });
    expect(calls).toContainEqual({
      table: "evidence_records",
      method: "select",
      args: ["id,content,source_label,created_at", { count: "exact" }],
    });
  });

  it("shows no workspace only when the user's active membership query is empty", async () => {
    const { client, calls } = makeClient({
      workspace_memberships: membership([]),
    });

    const result = await loadWorkspaceDashboard(client as never, USER_ID);

    expect(result).toEqual({ kind: "no-workspace" });
    expect(calls.some((call) => call.table === "evidence_records")).toBe(false);
  });

  it("fails closed when membership or workspace data is unavailable", async () => {
    const deniedClient = makeClient({
      workspace_memberships: membership(null, { message: "provider detail" }),
    });
    const missingWorkspaceClient = makeClient({
      workspace_memberships: membership([
        { workspace_id: OTHER_ID, role: "owner", created_at: "2026-10-01T00:00:00Z" },
      ]),
      workspaces: workspace([]),
    });

    expect(await loadWorkspaceDashboard(deniedClient.client as never, USER_ID)).toEqual({ kind: "unavailable" });
    expect(await loadWorkspaceDashboard(missingWorkspaceClient.client as never, USER_ID)).toEqual({ kind: "unavailable" });
  });

  it("does not convert an evidence service failure into a zero count", async () => {
    const { client } = makeClient({
      workspace_memberships: membership([
        { workspace_id: OTHER_ID, role: "member", created_at: "2026-10-01T00:00:00Z" },
      ]),
      workspaces: workspace([{ id: OTHER_ID, name: "Personal workspace" }]),
      evidence_records: evidence(null, null, { message: "provider detail" }),
    });

    const result = await loadWorkspaceDashboard(client as never, USER_ID);

    expect(result).toMatchObject({
      kind: "ready",
      dashboard: {
        role: "member",
        evidence: { kind: "unavailable" },
      },
    });
  });
});
