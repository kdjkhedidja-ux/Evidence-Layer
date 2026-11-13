import { beforeEach, describe, expect, it, vi } from "vitest";
import { GET as getApiKeys, POST as postApiKey } from "../app/api/evidence/me/api-keys/route";
import { DELETE as deleteApiKey } from "../app/api/evidence/me/api-keys/[keyId]/route";
import { GET as getMe } from "../app/api/evidence/me/route";
import { PUT as putPersonalWorkspace } from "../app/api/evidence/personal-workspace/route";
import { GET as getWorkspace } from "../app/api/evidence/workspaces/[workspaceId]/route";
import { POST as prepareTaskContext } from "../app/api/evidence/workspaces/[workspaceId]/task-context/route";
import { provisionPersonalWorkspaceForSession } from "../lib/evidence-api";

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
const AUTHENTICATED = {
  kind: "authenticated",
  user: { id: USER_ID },
  accessToken: "synthetic-test-access-token",
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

function request(path: string, init?: RequestInit): Request {
  return new Request(`https://app.example${path}`, init);
}

describe("same-origin Evidence Service proxy", () => {
  beforeEach(() => {
    mocks.getAuth.mockResolvedValue(AUTHENTICATED);
    mocks.serviceUrl.mockReturnValue("https://evidence.example");
    vi.stubGlobal("fetch", vi.fn());
  });

  it("forwards API key management only with a verified session and same origin", async () => {
    const fetchMock = vi.mocked(fetch).mockResolvedValue(Response.json([]));
    const response = await getApiKeys(request("/api/evidence/me/api-keys"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual([]);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://evidence.example/v1/me/api-keys",
      expect.objectContaining({
        method: "GET",
        headers: expect.objectContaining({
          Authorization: "Bearer synthetic-test-access-token",
        }),
      }),
    );

    const rejected = await postApiKey(request("/api/evidence/me/api-keys", {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: "https://attacker.example" },
      body: JSON.stringify({}),
    }));
    expect(rejected.status).toBe(403);
  });

  it("prepares task context only through the verified same-origin workspace boundary", async () => {
    const payload = {
      task: "Review the release",
      expected_outcome: "Identify risks",
      context_requirements: "Focus on rollback",
      retrieval: {
        status: "ok",
        query: "Review the release\nIdentify risks\nFocus on rollback",
        workspace_id: WORKSPACE_ID,
        selected_count: 1,
        used_tokens: 8,
        used_chars: 32,
        token_estimator: "chars_div_4",
        evidence: [{
          evidence_id: "10000000-0000-4000-8000-000000000003",
          content: "Rollback plan.",
          source_label: "runbook.md",
          source_id: null,
          source_version_id: null,
          score: 0.5,
          chunk_id: "10000000-0000-4000-8000-000000000003:0:16",
          token_count: 4,
          char_count: 16,
          path: "runbook.md",
          title: "Runbook",
        }],
      },
      relationships: [],
    };
    const fetchMock = vi.mocked(fetch).mockResolvedValue(Response.json(payload));
    const requestBody = {
      task: "Review the release",
      expected_outcome: "Identify risks",
      context_requirements: "Focus on rollback",
      top_k: 5,
      max_tokens: 1200,
      max_chars: 6000,
    };
    const response = await prepareTaskContext(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/task-context`, {
        method: "POST",
        headers: { Origin: "https://app.example", "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      }),
      { params: Promise.resolve({ workspaceId: WORKSPACE_ID }) },
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      `https://evidence.example/v1/workspaces/${WORKSPACE_ID}/task-context`,
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer synthetic-test-access-token",
        }),
        body: JSON.stringify(requestBody),
      }),
    );

    const rejected = await prepareTaskContext(
      request(`/api/evidence/workspaces/${WORKSPACE_ID}/task-context`, {
        method: "POST",
        headers: { Origin: "https://attacker.example", "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      }),
      { params: Promise.resolve({ workspaceId: WORKSPACE_ID }) },
    );
    expect(rejected.status).toBe(403);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("creates a personal API key and rejects malformed input", async () => {
    const key = {
      id: WORKSPACE_ID,
      workspace_id: WORKSPACE_ID,
      name: "Reporting export",
      key_prefix: "evl_pk_10000000",
      scopes: ["evidence:read", "relationships:read"],
      created_at: "2026-11-10T10:00:00Z",
      expires_at: "2026-12-10T10:00:00Z",
      revoked_at: null,
      last_used_at: null,
      token: `evl_pk_${WORKSPACE_ID}.${"A".repeat(43)}`,
    };
    vi.mocked(fetch).mockResolvedValue(Response.json(key, { status: 201 }));
    const created = await postApiKey(request("/api/evidence/me/api-keys", {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: "https://app.example" },
      body: JSON.stringify({ workspace_id: WORKSPACE_ID, name: "Reporting export", expiration_days: 30 }),
    }));
    expect(created.status).toBe(201);
    expect(await created.json()).toMatchObject({ name: "Reporting export", token: key.token });

    const invalid = await postApiKey(request("/api/evidence/me/api-keys", {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: "https://app.example" },
      body: JSON.stringify({ workspace_id: "not-a-uuid", name: "", expiration_days: 500 }),
    }));
    expect(invalid.status).toBe(400);
  });

  it("revokes one API key and returns only the confirmed identity", async () => {
    const fetchMock = vi.mocked(fetch).mockResolvedValue(Response.json({ id: WORKSPACE_ID, revoked: true }));
    const response = await deleteApiKey(
      request(`/api/evidence/me/api-keys/${WORKSPACE_ID}`, {
        method: "DELETE",
        headers: { Origin: "https://app.example" },
      }),
      { params: Promise.resolve({ keyId: WORKSPACE_ID }) },
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ id: WORKSPACE_ID, revoked: true });
    expect(fetchMock).toHaveBeenCalledWith(
      `https://evidence.example/v1/me/api-keys/${WORKSPACE_ID}`,
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("does not call the service without a verified user session", async () => {
    mocks.getAuth.mockResolvedValue({ kind: "unauthenticated" });
    const fetchMock = vi.mocked(fetch);
    const response = await getMe(request("/api/evidence/me"));
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ error: "Authentication is required." });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards only the verified session token and returns the verified user ID", async () => {
    const fetchMock = vi.mocked(fetch).mockResolvedValue(Response.json({
      user_id: USER_ID,
      issuer: "https://sample.supabase.co/auth/v1",
      workspaces: [{ id: WORKSPACE_ID, name: "Personal", role: "owner" }],
    }));
    const response = await getMe(request("/api/evidence/me"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      user_id: USER_ID,
      account_type: "individual",
      organization_name: null,
      can_manage_workspace: false,
      workspaces: [{ id: WORKSPACE_ID, name: "Personal", role: "owner" }],
    });
    const [, init] = fetchMock.mock.calls[0];
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer synthetic-test-access-token");
    expect(init).toMatchObject({ method: "GET", cache: "no-store", redirect: "manual" });
    expect(init?.signal).toBeInstanceOf(AbortSignal);
  });

  it("maps an invalid service token to a sanitized 401", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({
      message: "synthetic invalid token details",
      detail: "do not forward this",
    }, { status: 401 }));
    const response = await getMe(request("/api/evidence/me"));
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ error: "Your session is no longer valid." });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("does not accept an upstream identity that differs from the verified user", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({
      user_id: "20000000-0000-4000-8000-000000000001",
      issuer: "untrusted",
      workspaces: [],
    }));
    const response = await getMe(request("/api/evidence/me"));
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "Evidence service returned an invalid response." });
  });

  it.each(["TimeoutError", "AbortError"])(
    "maps a %s while reading an upstream body to service unavailable",
    async (name) => {
      const upstream = new Response(null, { status: 200 });
      vi.spyOn(upstream, "json").mockRejectedValue(new DOMException("Body read interrupted", name));
      vi.mocked(fetch).mockResolvedValue(upstream);

      const response = await getMe(request("/api/evidence/me"));
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({ error: "Evidence service is unavailable." });
    },
  );

  it("keeps malformed upstream JSON classified as an invalid response", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("{", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));

    const response = await getMe(request("/api/evidence/me"));
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "Evidence service returned an invalid response." });
  });

  it("refuses workspace provisioning without a matching Origin", async () => {
    const response = await putPersonalWorkspace(request("/api/evidence/personal-workspace", { method: "PUT" }));
    expect(response.status).toBe(403);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("provisions without accepting client-supplied identity or workspace data", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ id: WORKSPACE_ID, name: "Personal", role: "owner" }));
    const response = await putPersonalWorkspace(request("/api/evidence/personal-workspace", {
      method: "PUT",
      headers: { Origin: "https://app.example", "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: "attacker-choice", workspace_id: "attacker-choice" }),
    }));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ id: WORKSPACE_ID, name: "Personal", role: "owner" });
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("https://evidence.example/v1/me/personal-workspace");
    expect(init?.method).toBe("PUT");
    expect(init).not.toHaveProperty("body");
  });

  it("uses the confirmed session token and accepts repeated idempotent provisioning", async () => {
    const workspace = { id: WORKSPACE_ID, name: "Personal", role: "owner" };
    const fetchMock = vi.mocked(fetch).mockImplementation(async () => Response.json(workspace));

    expect(await provisionPersonalWorkspaceForSession("synthetic-session-token")).toBe(true);
    expect(await provisionPersonalWorkspaceForSession("synthetic-session-token")).toBe(true);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const [url, init] of fetchMock.mock.calls) {
      expect(url).toBe("https://evidence.example/v1/me/personal-workspace");
      expect(init?.method).toBe("PUT");
      expect(init).not.toHaveProperty("body");
      expect(init?.signal).toBeInstanceOf(AbortSignal);
      expect(new Headers(init?.headers).get("Authorization")).toBe(
        "Bearer synthetic-session-token",
      );
    }
  });

  it("fails closed on provisioning transport, service, or malformed-response failures", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ detail: "private" }, { status: 503 }));
    expect(await provisionPersonalWorkspaceForSession("synthetic-session-token")).toBe(false);

    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ id: "not-a-uuid", role: "owner" }));
    expect(await provisionPersonalWorkspaceForSession("synthetic-session-token")).toBe(false);

    vi.mocked(fetch).mockRejectedValueOnce(new Error("private network detail"));
    expect(await provisionPersonalWorkspaceForSession("synthetic-session-token")).toBe(false);
  });

  it("preserves workspace non-disclosure and strips provider errors", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ code: "PGRST116", details: "private" }, { status: 404 }));
    const response = await getWorkspace(request(`/api/evidence/workspaces/${WORKSPACE_ID}`), {
      params: Promise.resolve({ workspaceId: WORKSPACE_ID }),
    });
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "Workspace not found." });
  });

  it("returns sanitized service-unavailable errors for provider and transport failures", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ detail: "secret SQL detail" }, { status: 503 }));
    const providerResponse = await getMe(request("/api/evidence/me"));
    expect(providerResponse.status).toBe(503);
    expect(await providerResponse.json()).toEqual({ error: "Evidence service is unavailable." });

    vi.mocked(fetch).mockRejectedValueOnce(new Error("synthetic network detail"));
    const transportResponse = await getMe(request("/api/evidence/me"));
    expect(transportResponse.status).toBe(503);
    expect(await transportResponse.json()).toEqual({ error: "Evidence service is unavailable." });

    vi.mocked(fetch).mockRejectedValueOnce(new DOMException("Request timed out", "TimeoutError"));
    const timeoutResponse = await getMe(request("/api/evidence/me"));
    expect(timeoutResponse.status).toBe(503);
  });
});
