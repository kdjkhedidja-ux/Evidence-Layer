import { beforeEach, describe, expect, it, vi } from "vitest";
import { GET } from "../app/auth/callback/route";

const mocks = vi.hoisted(() => ({
  createClient: vi.fn(),
  getPublicConfig: vi.fn(),
  exchangeCodeForSession: vi.fn(),
  getUser: vi.fn(),
}));

vi.mock("../lib/supabase/server", () => ({ createSupabaseServerClient: () => mocks.createClient() }));
vi.mock("../lib/supabase/config", () => ({ getSupabasePublicConfig: () => mocks.getPublicConfig() }));

describe("Supabase PKCE callback", () => {
  beforeEach(() => {
    mocks.getPublicConfig.mockReturnValue({ url: "https://sample.supabase.co", publishableKey: "sb_publishable_synthetic" });
    mocks.exchangeCodeForSession.mockResolvedValue({ error: null });
    mocks.getUser.mockResolvedValue({ data: { user: { id: "synthetic-user" } }, error: null });
    mocks.createClient.mockResolvedValue({ auth: {
      exchangeCodeForSession: mocks.exchangeCodeForSession,
      getUser: mocks.getUser,
    } });
  });

  it("exchanges confirmation codes and only redirects to a safe app path", async () => {
    const response = await GET(new Request(
      "https://app.example/auth/callback?code=synthetic-code&next=%2F%23explorer",
    ));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("https://app.example/#explorer");
    expect(mocks.exchangeCodeForSession).toHaveBeenCalledWith("synthetic-code");
    expect(mocks.getUser).toHaveBeenCalledOnce();

    const external = await GET(new Request(
      "https://app.example/auth/callback?code=synthetic-code&next=https%3A%2F%2Foutside.example%2F",
    ));
    expect(external.headers.get("location")).toBe("https://app.example/");
  });

  it("routes password recovery back to the guarded reset page", async () => {
    const response = await GET(new Request(
      "https://app.example/auth/callback?code=synthetic-recovery-code&next=%2Freset-password",
    ));
    expect(response.headers.get("location")).toBe("https://app.example/reset-password");
  });

  it("maps callback query errors and invalid codes without exposing provider messages", async () => {
    const expired = await GET(new Request(
      "https://app.example/auth/callback?error=access_denied&error_code=otp_expired&error_description=private-details&next=%2Freset-password",
    ));
    expect(expired.headers.get("location")).toBe("https://app.example/auth/error?reason=expired-link");
    expect(expired.headers.get("location")).not.toContain("private-details");

    mocks.exchangeCodeForSession.mockResolvedValueOnce({ error: { message: "synthetic provider secret" } });
    const invalid = await GET(new Request("https://app.example/auth/callback?code=invalid-synthetic-code"));
    expect(invalid.headers.get("location")).toBe("https://app.example/auth/error?reason=invalid-link");
    expect(invalid.headers.get("location")).not.toContain("synthetic provider secret");
  });

  it("rejects callbacks with no code and handles missing configuration", async () => {
    const missing = await GET(new Request("https://app.example/auth/callback"));
    expect(missing.headers.get("location")).toBe("https://app.example/auth/error?reason=invalid-link");
    mocks.getPublicConfig.mockReturnValue(null);
    const unconfigured = await GET(new Request("https://app.example/auth/callback?code=synthetic-code"));
    expect(unconfigured.headers.get("location")).toBe("https://app.example/auth/error?reason=configuration");
  });
});
