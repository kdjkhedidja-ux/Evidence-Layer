import { beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "../app/api/auth/reset-password/route";
import { hasRecoveryProof, recoveryProof } from "../lib/supabase/recovery";

const mocks = vi.hoisted(() => ({
  context: vi.fn(),
  getCookie: vi.fn(),
  deleteCookie: vi.fn(),
  updateUser: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock("next/headers", () => ({
  cookies: async () => ({ get: mocks.getCookie, delete: mocks.deleteCookie }),
}));
vi.mock("../lib/supabase/server", () => ({ getVerifiedAuthContext: () => mocks.context() }));

function request(password = "new-synthetic-password", origin = "https://app.example") {
  return new Request("https://app.example/api/auth/reset-password", {
    method: "POST",
    headers: { origin, "content-type": "application/json" },
    body: JSON.stringify({ password }),
  });
}

describe("guarded password recovery", () => {
  beforeEach(async () => {
    mocks.context.mockResolvedValue({
      kind: "authenticated", accessToken: "synthetic-token-a",
      client: { auth: { updateUser: mocks.updateUser, signOut: mocks.signOut } },
    });
    mocks.getCookie.mockReturnValue({ value: await recoveryProof("synthetic-token-a") });
    mocks.updateUser.mockResolvedValue({ error: null });
    mocks.signOut.mockResolvedValue({ error: null });
  });

  it("binds recovery to the current session, then clears it and signs out", async () => {
    expect(await hasRecoveryProof(await recoveryProof("synthetic-token-a"), "synthetic-token-b")).toBe(false);
    const response = await POST(request());
    expect(response.status).toBe(200);
    expect(mocks.updateUser).toHaveBeenCalledWith({ password: "new-synthetic-password" });
    expect(mocks.deleteCookie).toHaveBeenCalledWith("evidence-layer-recovery");
    expect(mocks.signOut).toHaveBeenCalledWith({ scope: "local" });
  });

  it("rejects reused, switched-account, and missing recovery state", async () => {
    mocks.getCookie.mockReturnValueOnce(undefined);
    expect((await POST(request())).status).toBe(403);
    mocks.getCookie.mockReturnValueOnce({ value: await recoveryProof("synthetic-token-b") });
    expect((await POST(request())).status).toBe(403);
    mocks.context.mockResolvedValueOnce({ kind: "unauthenticated" });
    expect((await POST(request())).status).toBe(401);
    expect(mocks.updateUser).not.toHaveBeenCalled();
  });

  it("rejects a cross-origin password update before reading the session", async () => {
    const response = await POST(request("new-synthetic-password", "https://outside.example"));
    expect(response.status).toBe(403);
    expect(mocks.context).not.toHaveBeenCalled();
  });

  it("rejects weak passwords and handles provider failure safely", async () => {
    expect((await POST(request("short"))).status).toBe(400);
    mocks.updateUser.mockResolvedValueOnce({ error: { message: "private detail" } });
    const response = await POST(request());
    expect(response.status).toBe(400);
    expect(await response.text()).not.toContain("private detail");
    expect(mocks.signOut).not.toHaveBeenCalled();
  });

  it("distinguishes a completed password update from a failed automatic sign-out", async () => {
    mocks.signOut.mockResolvedValueOnce({ error: { message: "private sign-out detail" } });
    const response = await POST(request());
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      message: "Password changed, but sign out could not be confirmed.",
      code: "signout-unconfirmed",
    });
    expect(mocks.updateUser).toHaveBeenCalledOnce();
    expect(mocks.deleteCookie).toHaveBeenCalledWith("evidence-layer-recovery");
  });

  it("reports a completed password update when automatic sign-out throws", async () => {
    mocks.signOut.mockRejectedValueOnce(new Error("private transport detail"));
    const response = await POST(request());
    expect(response.status).toBe(503);
    expect((await response.json()).code).toBe("signout-unconfirmed");
    expect(mocks.updateUser).toHaveBeenCalledOnce();
  });
});
