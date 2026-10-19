import { beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "../app/api/auth/recovery-session/route";
import { hasRecoveryProof } from "../lib/supabase/recovery";

const mocks = vi.hoisted(() => ({
  createClient: vi.fn(),
  setSession: vi.fn(),
  getUser: vi.fn(),
  getSession: vi.fn(),
}));

vi.mock("../lib/supabase/server", () => ({
  createSupabaseServerClient: () => mocks.createClient(),
}));

function request(origin = "https://app.example", body: unknown = {
  accessToken: "synthetic-access-token",
  refreshToken: "synthetic-refresh-token",
}): Request {
  return new Request("https://app.example/api/auth/recovery-session", {
    method: "POST",
    headers: { origin, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("implicit recovery session handoff", () => {
  beforeEach(() => {
    mocks.setSession.mockResolvedValue({
      data: { session: { access_token: "synthetic-access-token" }, user: { id: "synthetic-user" } },
      error: null,
    });
    mocks.getUser.mockResolvedValue({ data: { user: { id: "synthetic-user" } }, error: null });
    mocks.getSession.mockResolvedValue({
      data: { session: { access_token: "synthetic-access-token", user: { id: "synthetic-user" } } },
      error: null,
    });
    mocks.createClient.mockResolvedValue({ auth: {
      setSession: mocks.setSession,
      getUser: mocks.getUser,
      getSession: mocks.getSession,
    } });
  });

  it("verifies the user and binds a short-lived proof to the recovered session", async () => {
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect(mocks.setSession).toHaveBeenCalledWith({
      access_token: "synthetic-access-token",
      refresh_token: "synthetic-refresh-token",
    });
    expect(mocks.getUser).toHaveBeenCalledOnce();
    const proof = response.cookies.get("evidence-layer-recovery");
    expect(proof?.httpOnly).toBe(true);
    expect(hasRecoveryProof(proof?.value, "synthetic-access-token")).toBe(true);
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("rejects cross-origin, malformed, and unverified sessions", async () => {
    expect((await POST(request("https://outside.example"))).status).toBe(403);
    expect(mocks.createClient).not.toHaveBeenCalled();
    expect((await POST(request("https://app.example", { accessToken: "missing-refresh" }))).status).toBe(400);
    mocks.getUser.mockResolvedValueOnce({ data: { user: { id: "different-user" } }, error: null });
    const response = await POST(request());
    expect(response.status).toBe(401);
    expect(response.cookies.get("evidence-layer-recovery")).toBeUndefined();
  });
});
