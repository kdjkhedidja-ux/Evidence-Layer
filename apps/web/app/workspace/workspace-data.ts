import type { SupabaseClient } from "@supabase/supabase-js";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const EVIDENCE_PREVIEW_LIMIT = 5;

export type WorkspaceEvidencePreview = {
  id: string;
  content: string;
  source_label: string | null;
  created_at: string;
};

export type WorkspaceDashboard = {
  workspace: {
    id: string;
    name: string;
  };
  role: "owner" | "member";
  evidence: {
    kind: "ready";
    count: number;
    records: WorkspaceEvidencePreview[];
  } | {
    kind: "unavailable";
  };
};

export type WorkspaceDashboardResult =
  | { kind: "ready"; dashboard: WorkspaceDashboard }
  | { kind: "no-workspace" }
  | { kind: "unavailable" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function parseMemberships(value: unknown): Array<{
  workspaceId: string;
  role: "owner" | "member";
}> | null {
  if (!Array.isArray(value)) return null;
  const memberships: Array<{ workspaceId: string; role: "owner" | "member" }> = [];
  for (const item of value) {
    if (
      !isRecord(item) ||
      typeof item.workspace_id !== "string" ||
      !UUID_PATTERN.test(item.workspace_id) ||
      (item.role !== "owner" && item.role !== "member")
    ) {
      return null;
    }
    memberships.push({ workspaceId: item.workspace_id, role: item.role });
  }
  return memberships;
}

function parseWorkspaces(value: unknown): Array<{ id: string; name: string }> | null {
  if (!Array.isArray(value)) return null;
  const workspaces: Array<{ id: string; name: string }> = [];
  for (const item of value) {
    if (
      !isRecord(item) ||
      typeof item.id !== "string" ||
      !UUID_PATTERN.test(item.id) ||
      typeof item.name !== "string" ||
      !item.name.trim() ||
      item.name.length > 120
    ) {
      return null;
    }
    workspaces.push({ id: item.id, name: item.name });
  }
  return workspaces;
}

function parseEvidence(value: unknown): WorkspaceEvidencePreview[] | null {
  if (!Array.isArray(value)) return null;
  const records: WorkspaceEvidencePreview[] = [];
  for (const item of value) {
    if (
      !isRecord(item) ||
      typeof item.id !== "string" ||
      !UUID_PATTERN.test(item.id) ||
      typeof item.content !== "string" ||
      !(item.source_label === null || typeof item.source_label === "string") ||
      typeof item.created_at !== "string" ||
      !Number.isFinite(Date.parse(item.created_at))
    ) {
      return null;
    }
    records.push({
      id: item.id,
      content: item.content,
      source_label: item.source_label,
      created_at: item.created_at,
    });
  }
  return records;
}

export async function loadWorkspaceDashboard(
  client: SupabaseClient,
  userId: string,
): Promise<WorkspaceDashboardResult> {
  let memberships: Array<{ workspaceId: string; role: "owner" | "member" }> | null = null;
  let workspaces: Array<{ id: string; name: string }> | null = null;

  try {
    const membershipResult = await client
      .from("workspace_memberships")
      .select("workspace_id,role,created_at")
      .eq("user_id", userId)
      .eq("status", "active")
      .order("created_at", { ascending: true })
      .limit(50);

    if (membershipResult.error) return { kind: "unavailable" };
    memberships = parseMemberships(membershipResult.data);
    if (!memberships) return { kind: "unavailable" };
    if (memberships.length === 0) return { kind: "no-workspace" };

    const workspaceResult = await client
      .from("workspaces")
      .select("id,name")
      .in("id", memberships.map((membership) => membership.workspaceId));

    if (workspaceResult.error) return { kind: "unavailable" };
    workspaces = parseWorkspaces(workspaceResult.data);
    if (!workspaces) return { kind: "unavailable" };
  } catch {
    return { kind: "unavailable" };
  }

  const workspaceById = new Map(workspaces.map((workspace) => [workspace.id, workspace]));
  const selectedMembership = memberships[0];
  const selectedWorkspace = workspaceById.get(selectedMembership.workspaceId);
  if (!selectedWorkspace) return { kind: "unavailable" };

  let evidence: WorkspaceDashboard["evidence"];
  try {
    const evidenceResult = await client
      .from("evidence_records")
      .select("id,content,source_label,created_at", { count: "exact" })
      .eq("workspace_id", selectedWorkspace.id)
      .order("created_at", { ascending: false })
      .limit(EVIDENCE_PREVIEW_LIMIT);

    const records = parseEvidence(evidenceResult.data);
    if (
      evidenceResult.error ||
      !records ||
      typeof evidenceResult.count !== "number" ||
      !Number.isSafeInteger(evidenceResult.count) ||
      evidenceResult.count < records.length
    ) {
      evidence = { kind: "unavailable" };
    } else {
      evidence = {
        kind: "ready",
        count: evidenceResult.count,
        records,
      };
    }
  } catch {
    evidence = { kind: "unavailable" };
  }

  return {
    kind: "ready",
    dashboard: {
      workspace: {
        id: selectedWorkspace.id,
        name: selectedWorkspace.name,
      },
      role: selectedMembership.role,
      evidence,
    },
  };
}
