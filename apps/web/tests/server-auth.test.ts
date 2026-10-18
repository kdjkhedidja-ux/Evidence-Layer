import { beforeEach, describe, expect, it, vi } from "vitest";
import { getVerifiedAuthContext } from "../lib/supabase/server";

const mocks = vi.hoisted(() => ({
  client: { auth: { getUser: vi.fn(), getSession: vi.fn() } },
  config: vi.fn(),
}));

vi.mock("@supabase/ssr", () => ({ createServerClient: () => mocks.client }));
vi.mock("next/headers", () => ({
  cookies: async () => ({ getAll: () => [], set: vi.fn() }),
}));
vi.mock("../lib/supabase/config", () => ({
  getSupabasePublicConfig: () => mocks.config(),
  SupabaseConfigurationError: class SupabaseConfigurationError extends Error {},
}));

describe("server-verified Supabase identity", () => {
  beforeEach(() => {
    mocks.config.mockReturnValue({ url: "https://sample.supabase.co", publishableKey: "sb_publishable_synthetic" });
    mocks.client.auth.getUser.mockResolvedValue({ data: { user: { id: "user-a" } }, error: null });
    mocks.client.auth.getSession.mockResolvedValue({
      data: { session: { user: { id: "user-a" }, access_token: "synthetic-access-token" } },
      error: null,
    });
  });

  it("verifies the user before retrieving a cookie session token", async () => {
    const result = await getVerifiedAuthContext();
    expect(result).toMatchObject({ kind: "authenticated", user: { id: "user-a" }, accessToken: "synthetic-access-token" });
    expect(mocks.client.auth.getUser.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.client.auth.getSession.mock.invocationCallOrder[0],
    );
  });

  it("does not forward a session token for a different user", async () => {
    mocks.client.auth.getSession.mockResolvedValueOnce({
      data: { session: { user: { id: "user-b" }, access_token: "synthetic-other-user-token" } },
      error: null,
    });
    expect(await getVerifiedAuthContext()).toEqual({ kind: "unauthenticated" });
  });

  it("rejects missing, invalid, or expired identities", async () => {
    mocks.client.auth.getUser.mockResolvedValueOnce({ data: { user: null }, error: { name: "AuthApiError" } });
    expect(await getVerifiedAuthContext()).toEqual({ kind: "unauthenticated" });
    mocks.client.auth.getUser.mockResolvedValueOnce({ data: { user: null }, error: null });
    expect(await getVerifiedAuthContext()).toEqual({ kind: "unauthenticated" });
  });
});
