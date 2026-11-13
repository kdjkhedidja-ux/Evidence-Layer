import { beforeEach, describe, expect, it, vi } from "vitest";
import { GET as getEvidence } from "../app/api/evidence/workspaces/[workspaceId]/evidence/route";
import { POST as postEvidence } from "../app/api/evidence/workspaces/[workspaceId]/evidence/route";
import { PATCH as patchEvidence } from "../app/api/evidence/workspaces/[workspaceId]/evidence/[evidenceId]/route";
import { DELETE as deleteEvidence } from "../app/api/evidence/workspaces/[workspaceId]/evidence/[evidenceId]/route";
import { GET as getSourceContent } from "../app/api/evidence/workspaces/[workspaceId]/sources/[sourceId]/versions/[versionId]/content/route";

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
const WORKSPACE_ID = "10000000-0000-4000-8000-000000000002";
const EVIDENCE_ID = "10000000-0000-4000-8000-000000000003";
const SOURCE_ID = "10000000-0000-4000-8000-000000000004";
const VERSION_ID = "10000000-0000-4000-8000-000000000005";
const TOKEN = "synthetic-user-access-token";
const AUTHENTICATED = {
  kind: "authenticated",
  user: { id: USER_ID },
  accessToken: TOKEN,
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
const RECORD = {
  id: EVIDENCE_ID,
  workspace_id: WORKSPACE_ID,
  content: "Synthetic integration evidence",
  source_label: "test fixture",
  source_id: null,
  source_version_id: null,
  created_at: "2026-10-31T12:00:00Z",
  updated_at: "2026-10-31T12:00:00Z",
};

function request(path: string, init?: RequestInit): Request {
  return new Request(`https://app.example${path}`, init);
}

function params<T extends Record<string, string>>(value: T): { params: Promise<T> } {
  return { params: Promise.resolve(value) };
}

function sameOriginHeaders(): HeadersInit {
  return { Origin: "https://app.example", "Content-Type": "application/json" };
}

describe("workspace evidence and verified source Next.js boundary", () => {
  beforeEach(() => {
    mocks.getAuth.mockResolvedValue(AUTHENTICATED);
    mocks.serviceUrl.mockReturnValue("https://evidence.example");
    vi.stubGlobal("fetch", vi.fn());
  });

  it("loads only records returned for the requested workspace and forwards the verified token", async () => {
    const fetchMock = vi.mocked(fetch).mockResolvedValue(Response.json([RECORD]));
    const response = await getEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence`),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual([RECORD]);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`https://evidence.example/v1/workspaces/${WORKSPACE_ID}/evidence`);
    expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer ${TOKEN}`);
    expect(init).toMatchObject({ method: "GET", cache: "no-store", redirect: "manual" });
  });

  it("does not contact the service without a verified session", async () => {
    mocks.getAuth.mockResolvedValue({ kind: "unauthenticated" });
    const response = await getEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence`),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(response.status).toBe(401);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("rejects evidence belonging to another workspace before returning it", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json([
      { ...RECORD, workspace_id: "20000000-0000-4000-8000-000000000001" },
    ]));
    const response = await getEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence`),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "Evidence service returned an invalid response." });
  });

  it("does not forward unrecognized create fields or allow writes without same-origin", async () => {
    const invalidBody = await postEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence`, {
        method: "POST",
        headers: sameOriginHeaders(),
        body: JSON.stringify({ content: "test", workspace_id: "attacker-selected" }),
      }),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(invalidBody.status).toBe(400);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();

    const crossOrigin = await postEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Origin: "https://evil.example" },
        body: JSON.stringify({ content: "test" }),
      }),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(crossOrigin.status).toBe(403);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();

    const oversized = await postEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence`, {
        method: "POST",
        headers: sameOriginHeaders(),
        body: JSON.stringify({ content: "x".repeat(270_001) }),
      }),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(oversized.status).toBe(400);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("creates through the existing workspace-scoped service contract and returns no credential", async () => {
    const fetchMock = vi.mocked(fetch).mockResolvedValue(Response.json(RECORD, { status: 201 }));
    const response = await postEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence`, {
        method: "POST",
        headers: sameOriginHeaders(),
        body: JSON.stringify({ content: "  New evidence  ", source_label: "  Meeting note  " }),
      }),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(response.status).toBe(201);
    const body = (await response.json()) as Record<string, unknown>;
    expect(body.content).toBe(RECORD.content);
    expect(JSON.stringify(body)).not.toContain(TOKEN);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`https://evidence.example/v1/workspaces/${WORKSPACE_ID}/evidence`);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ content: "New evidence", source_label: "Meeting note" });
  });

  it("patches only allow-listed fields and maps database denials to a non-disclosing 404", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(Response.json(RECORD));
    const updated = await patchEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence/${EVIDENCE_ID}`, {
        method: "PATCH",
        headers: sameOriginHeaders(),
        body: JSON.stringify({ source_label: "Revised label" }),
      }),
      params({ workspaceId: WORKSPACE_ID, evidenceId: EVIDENCE_ID }),
    );
    expect(updated.status).toBe(200);
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(JSON.parse(String(init?.body))).toEqual({ source_label: "Revised label" });

    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ detail: "private RLS detail" }, { status: 404 }));
    const denied = await patchEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence/${EVIDENCE_ID}`, {
        method: "PATCH",
        headers: sameOriginHeaders(),
        body: JSON.stringify({ content: "changed" }),
      }),
      params({ workspaceId: WORKSPACE_ID, evidenceId: EVIDENCE_ID }),
    );
    expect(denied.status).toBe(404);
    expect(await denied.json()).toEqual({ error: "Evidence record not found." });
  });

  it("deletes only after same-origin validation and never forwards service error bodies", async () => {
    const crossOrigin = await deleteEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence/${EVIDENCE_ID}`, {
        method: "DELETE",
        headers: { Origin: "https://evil.example" },
      }),
      params({ workspaceId: WORKSPACE_ID, evidenceId: EVIDENCE_ID }),
    );
    expect(crossOrigin.status).toBe(403);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();

    vi.mocked(fetch).mockResolvedValue(Response.json({ detail: "private database details" }, { status: 503 }));
    const failed = await deleteEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence/${EVIDENCE_ID}`, {
        method: "DELETE",
        headers: sameOriginHeaders(),
      }),
      params({ workspaceId: WORKSPACE_ID, evidenceId: EVIDENCE_ID }),
    );
    expect(failed.status).toBe(503);
    expect(await failed.json()).toEqual({ error: "Evidence service is unavailable." });
  });

  it("reads source bytes only through the existing rechecking service endpoint", async () => {
    const bytes = new TextEncoder().encode("verified fixture source").buffer;
    const fetchMock = vi.mocked(fetch).mockResolvedValue(new Response(bytes, {
      status: 200,
      headers: {
        "Content-Type": "application/octet-stream",
        "Content-Digest": "sha-256=:YWJjZA==:",
      },
    }));
    const response = await getSourceContent(
      request(
        "/api/evidence/workspaces/" + WORKSPACE_ID + "/sources/" + SOURCE_ID +
          "/versions/" + VERSION_ID + "/content",
      ),
      params({ workspaceId: WORKSPACE_ID, sourceId: SOURCE_ID, versionId: VERSION_ID }),
    );
    expect(response.status).toBe(200);
    expect(await response.text()).toBe("verified fixture source");
    expect(response.headers.get("Content-Digest")).toBe("sha-256=:YWJjZA==:");
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "https://evidence.example/v1/workspaces/" + WORKSPACE_ID + "/sources/" +
        SOURCE_ID + "/versions/" + VERSION_ID + "/content",
    );
    expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer ${TOKEN}`);
  });

  it("returns no content when the service denies the source grant or is unavailable", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ detail: "private denial detail" }, { status: 403 }));
    const denied = await getSourceContent(
      request("/api/source", { headers: { Origin: "https://app.example" } }),
      params({ workspaceId: WORKSPACE_ID, sourceId: SOURCE_ID, versionId: VERSION_ID }),
    );
    expect(denied.status).toBe(403);
    expect(await denied.json()).toEqual({ error: "Source read is not authorized." });

    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ detail: "private storage detail" }, { status: 503 }));
    const unavailable = await getSourceContent(
      request("/api/source", { headers: { Origin: "https://app.example" } }),
      params({ workspaceId: WORKSPACE_ID, sourceId: SOURCE_ID, versionId: VERSION_ID }),
    );
    expect(unavailable.status).toBe(503);
    expect(await unavailable.json()).toEqual({ error: "Verified source content is unavailable." });
  });

  it("rejects malformed source IDs and malformed source representations", async () => {
    const invalidId = await getSourceContent(
      request("/api/source", { headers: { Origin: "https://app.example" } }),
      params({ workspaceId: WORKSPACE_ID, sourceId: "not-a-uuid", versionId: VERSION_ID }),
    );
    expect(invalidId.status).toBe(404);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();

    vi.mocked(fetch).mockResolvedValue(new Response("<script>not source bytes</script>", {
      headers: { "Content-Type": "text/html" },
    }));
    const invalidRepresentation = await getSourceContent(
      request("/api/source", { headers: { Origin: "https://app.example" } }),
      params({ workspaceId: WORKSPACE_ID, sourceId: SOURCE_ID, versionId: VERSION_ID }),
    );
    expect(invalidRepresentation.status).toBe(502);
    expect(await invalidRepresentation.json()).toEqual({ error: "Evidence service returned an invalid response." });
  });
});

describe("workspace sources index Next.js boundary", () => {
  beforeEach(() => {
    mocks.getAuth.mockResolvedValue(AUTHENTICATED);
    mocks.serviceUrl.mockReturnValue("https://evidence.example");
    vi.stubGlobal("fetch", vi.fn());
  });

  const VERSION_SUMMARY = {
    source_id: SOURCE_ID,
    version_id: VERSION_ID,
    state: "published",
    sha256: "a".repeat(64),
    size_bytes: 128,
    created_at: "2026-10-31T12:00:00Z",
  };

  it("lists visible source versions for the verified session", async () => {
    const fetchMock = vi.mocked(fetch).mockResolvedValue(Response.json([VERSION_SUMMARY]));
    const { GET: getSources } = await import(
      "../app/api/evidence/workspaces/[workspaceId]/sources/route"
    );
    const response = await getSources(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/sources`),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual([VERSION_SUMMARY]);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`https://evidence.example/v1/workspaces/${WORKSPACE_ID}/sources`);
    expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer ${TOKEN}`);
  });

  it("requires authentication and never calls the service without a session", async () => {
    mocks.getAuth.mockResolvedValue({ kind: "unauthenticated" });
    const { GET: getSources } = await import(
      "../app/api/evidence/workspaces/[workspaceId]/sources/route"
    );
    const response = await getSources(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/sources`),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(response.status).toBe(401);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("maps a non-member 404 without disclosing workspace existence", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ detail: "Workspace not found" }, { status: 404 }));
    const { GET: getSources } = await import(
      "../app/api/evidence/workspaces/[workspaceId]/sources/route"
    );
    const response = await getSources(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/sources`),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "Workspace not found." });
  });

  it("rejects a malformed service payload as 502 and leaks nothing", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json([{ ...VERSION_SUMMARY, sha256: "bad" }]));
    const { GET: getSources } = await import(
      "../app/api/evidence/workspaces/[workspaceId]/sources/route"
    );
    const response = await getSources(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/sources`),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(response.status).toBe(502);
    expect(JSON.stringify(await response.json())).not.toContain("bad");
  });

  it("rejects a non-UUID workspace id before any service call", async () => {
    const { GET: getSources } = await import(
      "../app/api/evidence/workspaces/[workspaceId]/sources/route"
    );
    const response = await getSources(
      request("/api/evidence/workspaces/not-a-uuid/sources"),
      params({ workspaceId: "not-a-uuid" }),
    );
    expect(response.status).toBe(404);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});

describe("evidence source link Next.js boundary", () => {
  beforeEach(() => {
    mocks.getAuth.mockResolvedValue(AUTHENTICATED);
    mocks.serviceUrl.mockReturnValue("https://evidence.example");
    vi.stubGlobal("fetch", vi.fn());
  });

  it("forwards a complete provenance link on create", async () => {
    const linked = { ...RECORD, source_id: SOURCE_ID, source_version_id: VERSION_ID };
    const fetchMock = vi.mocked(fetch).mockResolvedValue(Response.json(linked, { status: 201 }));
    const response = await postEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence`, {
        method: "POST",
        headers: sameOriginHeaders(),
        body: JSON.stringify({
          content: "Linked evidence",
          source_id: SOURCE_ID,
          source_version_id: VERSION_ID,
        }),
      }),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(response.status).toBe(201);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      content: "Linked evidence",
      source_id: SOURCE_ID,
      source_version_id: VERSION_ID,
    });
  });

  it("rejects a one-sided link before any service call", async () => {
    const response = await postEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence`, {
        method: "POST",
        headers: sameOriginHeaders(),
        body: JSON.stringify({ content: "Linked evidence", source_id: SOURCE_ID }),
      }),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(response.status).toBe(400);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("forwards link clearing as an explicit null pair", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(Response.json(RECORD));
    const response = await patchEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence/${EVIDENCE_ID}`, {
        method: "PATCH",
        headers: sameOriginHeaders(),
        body: JSON.stringify({ source_id: null, source_version_id: null }),
      }),
      params({ workspaceId: WORKSPACE_ID, evidenceId: EVIDENCE_ID }),
    );
    expect(response.status).toBe(200);
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual({
      source_id: null,
      source_version_id: null,
    });
  });

  it("rejects a link with non-UUID identifiers", async () => {
    const response = await postEvidence(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/evidence`, {
        method: "POST",
        headers: sameOriginHeaders(),
        body: JSON.stringify({
          content: "Linked evidence",
          source_id: "../../etc",
          source_version_id: VERSION_ID,
        }),
      }),
      params({ workspaceId: WORKSPACE_ID }),
    );
    expect(response.status).toBe(400);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});
