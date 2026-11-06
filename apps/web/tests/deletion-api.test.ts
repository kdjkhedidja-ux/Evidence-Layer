import { beforeEach, describe, expect, it, vi } from "vitest";
import { POST as createDeletion } from "../app/api/evidence/deletion-requests/route";
import { GET as getDeletion } from "../app/api/evidence/deletion-requests/[requestId]/route";
import { POST as acceptDeletion } from "../app/api/evidence/deletion-requests/[requestId]/accept/route";

const mocks = vi.hoisted(() => ({ getAuth: vi.fn(), serviceUrl: vi.fn() }));

vi.mock("../lib/supabase/server", () => ({ getVerifiedAuthContext: () => mocks.getAuth() }));
vi.mock("../lib/supabase/config", () => ({ getEvidenceServiceBaseUrl: () => mocks.serviceUrl() }));

const USER_ID = "10000000-0000-4000-8000-000000000001";
const REQUEST_ID = "72000000-0000-4000-8000-000000000001";
const TOKEN = "synthetic-user-access-token";
const AUTHENTICATED = { kind: "authenticated", user: { id: USER_ID }, accessToken: TOKEN, client: {} };
const VIEW = {
  request_id: REQUEST_ID,
  requester_user_id: USER_ID,
  scope_type: "account",
  status: "approval_pending",
  created_at: "2026-11-06T12:00:00Z",
  accepted_at: null,
};

function request(path: string, init?: RequestInit): Request {
  return new Request(`https://app.example${path}`, init);
}
function params<T extends Record<string, string>>(value: T) { return { params: Promise.resolve(value) }; }
const sameOrigin = { Origin: "https://app.example", "Content-Type": "application/json" };

describe("deletion request Next.js boundary", () => {
  beforeEach(() => {
    mocks.getAuth.mockResolvedValue(AUTHENTICATED);
    mocks.serviceUrl.mockReturnValue("https://evidence.example");
    vi.stubGlobal("fetch", vi.fn());
  });

  it("forwards the user token and idempotency key when creating a request", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json(VIEW, { status: 201 }));
    const response = await createDeletion(request("/api/evidence/deletion-requests", {
      method: "POST", headers: sameOrigin, body: JSON.stringify({ idempotency_key: "key-1" }),
    }));
    expect(response.status).toBe(201);
    expect(await response.json()).toEqual(VIEW);
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("https://evidence.example/v1/deletion-requests");
    expect(init?.headers).toMatchObject({ Authorization: `Bearer ${TOKEN}` });
    expect(JSON.parse(String(init?.body))).toEqual({ idempotency_key: "key-1" });
  });

  it("rejects a cross-origin create and invalid keys before calling upstream", async () => {
    const crossOrigin = await createDeletion(request("/api/evidence/deletion-requests", {
      method: "POST", headers: { Origin: "https://evil.example", "Content-Type": "application/json" },
      body: JSON.stringify({ idempotency_key: "key-1" }),
    }));
    expect(crossOrigin.status).toBe(403);

    const invalid = await createDeletion(request("/api/evidence/deletion-requests", {
      method: "POST", headers: sameOrigin, body: JSON.stringify({ idempotency_key: "  " }),
    }));
    expect(invalid.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reads and accepts a request with the user token", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(Response.json(VIEW));
    const statusResponse = await getDeletion(request(`/api/evidence/deletion-requests/${REQUEST_ID}`), params({ requestId: REQUEST_ID }));
    expect(statusResponse.status).toBe(200);

    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ ...VIEW, status: "journal_ack_pending", accepted_at: "2026-11-06T12:01:00Z" }));
    const acceptResponse = await acceptDeletion(request(`/api/evidence/deletion-requests/${REQUEST_ID}/accept`, {
      method: "POST", headers: sameOrigin,
    }), params({ requestId: REQUEST_ID }));
    expect(acceptResponse.status).toBe(200);
    expect((await acceptResponse.json()).status).toBe("journal_ack_pending");
    expect(vi.mocked(fetch).mock.calls[1][0]).toBe(`https://evidence.example/v1/deletion-requests/${REQUEST_ID}/accept`);
  });

  it("maps an open-request conflict and rejects a foreign requester", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ error: "conflict" }, { status: 409 }));
    const conflict = await createDeletion(request("/api/evidence/deletion-requests", {
      method: "POST", headers: sameOrigin, body: JSON.stringify({ idempotency_key: "key-2" }),
    }));
    expect(conflict.status).toBe(409);

    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ ...VIEW, requester_user_id: "other-user" }));
    const foreign = await getDeletion(request(`/api/evidence/deletion-requests/${REQUEST_ID}`), params({ requestId: REQUEST_ID }));
    expect(foreign.status).toBe(502);
  });
});
