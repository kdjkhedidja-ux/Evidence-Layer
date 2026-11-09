import { beforeEach, describe, expect, it, vi } from "vitest";
import { GET as getMe } from "../app/api/evidence/me/route";
import { PUT as putPersonalWorkspace } from "../app/api/evidence/personal-workspace/route";
import { GET as getWorkspace } from "../app/api/evidence/workspaces/[workspaceId]/route";
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
  client: {},
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
