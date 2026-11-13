import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "../middleware";
import { recoveryProof, RECOVERY_COOKIE } from "../lib/supabase/recovery";

const mocks = vi.hoisted(() => ({
  user: null as { id: string } | null,
  error: null as { name: string } | null,
  config: vi.fn(),
}));

vi.mock("@supabase/ssr", () => ({
  createServerClient: () => ({
    auth: {
      getUser: async () => ({ data: { user: mocks.user }, error: mocks.error }),
      getSession: async () => ({ data: { session: { access_token: "synthetic-access-token" } }, error: null }),
    },
  }),
}));
vi.mock("../lib/supabase/config", () => ({ getSupabasePublicConfig: () => mocks.config() }));

describe("server route protection and session refresh", () => {
  beforeEach(() => {
    mocks.config.mockReturnValue({ url: "https://sample.supabase.co", publishableKey: "sb_publishable_synthetic" });
    mocks.user = null;
    mocks.error = null;
  });

  it("allows the public synthetic Explorer without an account", async () => {
    const response = await middleware(new NextRequest("https://app.example/"));
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("allows a server-verified session to reach the Explorer", async () => {
    mocks.user = { id: "synthetic-user" };
    const response = await middleware(new NextRequest("https://app.example/"));
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("redirects expired reset links and fails closed if session refresh is unavailable", async () => {
    const reset = await middleware(new NextRequest("https://app.example/reset-password"));
    expect(reset.headers.get("location")).toBe("https://app.example/auth/error?reason=expired-link");

    mocks.error = { name: "AuthRetryableFetchError" };
    const recovery = await middleware(new NextRequest("https://app.example/reset-password"));
    expect(recovery.headers.get("location")).toBe("https://app.example/auth/error?reason=configuration");
  });

  it("rejects signed-in users without a valid recovery proof", async () => {
    mocks.user = { id: "synthetic-user" };
    const response = await middleware(new NextRequest("https://app.example/reset-password"));
    expect(response.headers.get("location")).toBe("https://app.example/auth/error?reason=invalid-link");

    const valid = new NextRequest("https://app.example/reset-password");
    valid.cookies.set(RECOVERY_COOKIE, recoveryProof("synthetic-access-token"));
    const allowed = await middleware(valid);
    expect(allowed.headers.get("x-middleware-next")).toBe("1");
  });

  it("keeps recovery protected when Supabase config is missing", async () => {
    mocks.config.mockReturnValue(null);
    const response = await middleware(new NextRequest("https://app.example/reset-password"));
    expect(response.headers.get("location")).toBe("https://app.example/auth/error?reason=configuration");
  });
});
