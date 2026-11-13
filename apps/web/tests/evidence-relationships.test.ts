import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  DELETE,
  PATCH,
} from "../app/api/evidence/workspaces/[workspaceId]/relationships/[relationshipId]/route";
import {
  GET,
  POST,
} from "../app/api/evidence/workspaces/[workspaceId]/relationships/route";
import { parseRelationshipMutation } from "../lib/evidence-api";

const mocks = vi.hoisted(() => ({
  getAuth: vi.fn(),
  serviceUrl: vi.fn(),
}));

vi.mock("../lib/supabase/server", () => ({
  getVerifiedAuthContext: () => mocks.getAuth(),
}));
vi.mock("../lib/supabase/config", () => ({
  getEvidenceServiceBaseUrl: () => mocks.serviceUrl(),
}));

const USER_ID = "10000000-0000-4000-8000-000000000001";
const WORKSPACE_ID = "a1000000-0000-4000-8000-000000000001";
const EVIDENCE_A = "e1000000-0000-4000-8000-000000000001";
const EVIDENCE_B = "e2000000-0000-4000-8000-000000000002";
const RELATION_ID = "f1000000-0000-4000-8000-000000000001";
const AUTHENTICATED = {
  kind: "authenticated",
  user: { id: USER_ID },
  accessToken: "synthetic-session-token",
  client: {
    auth: {
      mfa: {
        getAuthenticatorAssuranceLevel: async () => ({
          data: { currentLevel: "aal1", nextLevel: "aal1" },
          error: null,
        }),
      },
    },
  },
};

function request(path: string, method: string, body?: unknown): Request {
  return new Request(`https://app.example${path}`, {
    method,
    headers: {
      Origin: "https://app.example",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}

const relationship = {
  id: RELATION_ID,
  workspace_id: WORKSPACE_ID,
  evidence_id_a: EVIDENCE_A,
  evidence_id_b: EVIDENCE_B,
  relation_type: "contradicts",
  rationale: "The stored records state different dates.",
  provenance_note: "Manual comparison of both original records.",
  created_by: USER_ID,
  created_at: "2026-11-02T12:00:00Z",
  updated_by: USER_ID,
  updated_at: "2026-11-02T12:00:00Z",
};

describe("workspace relationship API routes", () => {
  beforeEach(() => {
    mocks.getAuth.mockResolvedValue(AUTHENTICATED);
    mocks.serviceUrl.mockReturnValue("https://evidence.example");
    vi.stubGlobal("fetch", vi.fn());
  });

  it("requires a verified user session before reaching the service", async () => {
    mocks.getAuth.mockResolvedValue({ kind: "unauthenticated" });
    const response = await GET(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/relationships`, "GET"),
      { params: Promise.resolve({ workspaceId: WORKSPACE_ID }) },
    );
    expect(response.status).toBe(401);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("fails closed when the MFA assurance level is unavailable", async () => {
    mocks.getAuth.mockResolvedValue({
      ...AUTHENTICATED,
      client: {
        auth: {
          mfa: {
            getAuthenticatorAssuranceLevel: async () => ({
              data: null,
              error: { message: "private provider detail" },
            }),
          },
        },
      },
    });

    const response = await GET(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/relationships`, "GET"),
      { params: Promise.resolve({ workspaceId: WORKSPACE_ID }) },
    );

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      error: "Two-factor authentication status is unavailable.",
      code: "mfa_unavailable",
    });
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("returns only validated relationships for the selected workspace", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json([relationship]));
    const response = await GET(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/relationships`, "GET"),
      { params: Promise.resolve({ workspaceId: WORKSPACE_ID }) },
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual([relationship]);
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      `https://evidence.example/v1/workspaces/${WORKSPACE_ID}/relationships`,
      expect.objectContaining({ method: "GET", cache: "no-store", redirect: "manual" }),
    );
  });

  it("rejects cross-workspace relationship data from the service", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json([{ ...relationship, workspace_id: "b3000000-0000-4000-8000-000000000003" }]));
    const response = await GET(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/relationships`, "GET"),
      { params: Promise.resolve({ workspaceId: WORKSPACE_ID }) },
    );
    expect(response.status).toBe(502);
  });

  it("forwards owner mutations only with same-origin requests and verified auth", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json(relationship, { status: 201 }));
    const body = {
      evidence_id_a: EVIDENCE_A,
      evidence_id_b: EVIDENCE_B,
      relation_type: "contradicts",
      rationale: relationship.rationale,
      provenance_note: relationship.provenance_note,
    };
    const response = await POST(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/relationships`, "POST", body),
      { params: Promise.resolve({ workspaceId: WORKSPACE_ID }) },
    );
    expect(response.status).toBe(201);
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer synthetic-session-token");
    expect(JSON.parse(String(init?.body))).toEqual(body);
  });

  it("maps denied, conflicting, and unavailable mutations without forwarding provider details", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ detail: "private" }, { status: 403 }));
    const denied = await DELETE(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/relationships/${RELATION_ID}`, "DELETE"),
      { params: Promise.resolve({ workspaceId: WORKSPACE_ID, relationshipId: RELATION_ID }) },
    );
    expect(denied.status).toBe(403);
    expect(await denied.json()).toEqual({ error: "This action is not available for this account." });

    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ detail: "private" }, { status: 409 }));
    const conflict = await POST(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/relationships`, "POST", {
        evidence_id_a: EVIDENCE_A,
        evidence_id_b: EVIDENCE_B,
        relation_type: "supports",
        rationale: "reason",
        provenance_note: "basis",
      }),
      { params: Promise.resolve({ workspaceId: WORKSPACE_ID }) },
    );
    expect(conflict.status).toBe(409);
    expect((await conflict.text()).includes("private")).toBe(false);
  });

  it("rejects malformed or oversized relationship payloads", async () => {
    const duplicate = await parseRelationshipMutation(request("/x", "POST", {
      evidence_id_a: EVIDENCE_A,
      evidence_id_b: EVIDENCE_A,
      relation_type: "supports",
      rationale: "reason",
      provenance_note: "basis",
    }), "create");
    expect(duplicate.ok).toBe(false);

    const extra = await parseRelationshipMutation(request("/x", "PATCH", { rationale: "reason", owner: true }), "update");
    expect(extra.ok).toBe(false);

    const oversized = new Request("https://app.example/x", {
      method: "POST",
      headers: { "Content-Length": "20000", "Content-Type": "application/json" },
      body: JSON.stringify({ rationale: "x".repeat(20000) }),
    });
    expect((await parseRelationshipMutation(oversized, "create")).ok).toBe(false);
  });

  it("supports owner updates but never lets a client reassign relationship evidence", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ ...relationship, rationale: "Revised rationale." }));
    const response = await PATCH(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/relationships/${RELATION_ID}`, "PATCH", { rationale: "Revised rationale." }),
      { params: Promise.resolve({ workspaceId: WORKSPACE_ID, relationshipId: RELATION_ID }) },
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ rationale: "Revised rationale." });
    const parsed = await parseRelationshipMutation(request("/x", "PATCH", { evidence_id_a: EVIDENCE_A, rationale: "no" }), "update");
    expect(parsed.ok).toBe(false);
  });
});
