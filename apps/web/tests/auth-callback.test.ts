import { beforeEach, describe, expect, it, vi } from "vitest";
import { GET } from "../app/auth/callback/route";

const mocks = vi.hoisted(() => ({
  createClient: vi.fn(),
  getPublicConfig: vi.fn(),
  exchangeCodeForSession: vi.fn(),
  verifyOtp: vi.fn(),
  getUser: vi.fn(),
  provisionPersonalWorkspace: vi.fn(),
}));

vi.mock("../lib/supabase/server", () => ({ createSupabaseServerClient: () => mocks.createClient() }));
vi.mock("../lib/supabase/config", () => ({ getSupabasePublicConfig: () => mocks.getPublicConfig() }));
vi.mock("../lib/evidence-api", () => ({
  provisionPersonalWorkspaceForSession: (token: string) => mocks.provisionPersonalWorkspace(token),
}));

describe("Supabase PKCE callback", () => {
  beforeEach(() => {
    mocks.getPublicConfig.mockReturnValue({ url: "https://sample.supabase.co", publishableKey: "sb_publishable_synthetic" });
    mocks.exchangeCodeForSession.mockResolvedValue({
      data: { session: { access_token: "synthetic-session-token", user: { id: "synthetic-user" } }, redirectType: null },
      error: null,
    });
    mocks.getUser.mockResolvedValue({ data: { user: { id: "synthetic-user" } }, error: null });
    mocks.verifyOtp.mockResolvedValue({
      data: { session: { access_token: "synthetic-session-token", user: { id: "synthetic-user" } } },
      error: null,
    });
    mocks.provisionPersonalWorkspace.mockResolvedValue(true);
    mocks.createClient.mockResolvedValue({ auth: {
      exchangeCodeForSession: mocks.exchangeCodeForSession,
      verifyOtp: mocks.verifyOtp,
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
    expect(mocks.provisionPersonalWorkspace).toHaveBeenCalledWith("synthetic-session-token");

    const external = await GET(new Request(
      "https://app.example/auth/callback?code=synthetic-code&next=https%3A%2F%2Foutside.example%2F",
    ));
    expect(external.headers.get("location")).toBe("https://app.example/");
  });

  it("verifies an email-confirmation OTP and provisions the confirmed user", async () => {
    const response = await GET(new Request(
      "https://app.example/auth/callback?token_hash=synthetic-confirmation-hash&type=email&next=%2F",
    ));

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("https://app.example/");
    expect(mocks.verifyOtp).toHaveBeenCalledWith({
      token_hash: "synthetic-confirmation-hash",
      type: "email",
    });
    expect(mocks.getUser).toHaveBeenCalledOnce();
    expect(mocks.provisionPersonalWorkspace).toHaveBeenCalledWith("synthetic-session-token");
  });

  it("routes password recovery back to the guarded reset page", async () => {
    mocks.exchangeCodeForSession.mockResolvedValueOnce({
      data: { session: { access_token: "synthetic-session-token", user: { id: "synthetic-user" } }, redirectType: "recovery" },
      error: null,
    });
    const response = await GET(new Request(
      "https://app.example/auth/callback?code=synthetic-recovery-code&sb_flow_id=synthetic-flow-id",
    ));
    expect(response.headers.get("location")).toBe("https://app.example/reset-password");
    expect(mocks.exchangeCodeForSession).toHaveBeenCalledWith("synthetic-recovery-code", { flowId: "synthetic-flow-id" });
    expect(mocks.provisionPersonalWorkspace).not.toHaveBeenCalled();
    expect(response.cookies.get("evidence-layer-recovery")?.httpOnly).toBe(true);
    expect(response.cookies.get("evidence-layer-recovery")?.value).not.toContain("synthetic-session-token");
  });

  it("does not turn a confirmation code or email OTP into password recovery through next", async () => {
    const codeResponse = await GET(new Request(
      "https://app.example/auth/callback?code=synthetic-confirmation-code&next=%2Freset-password",
    ));
    const otpResponse = await GET(new Request(
      "https://app.example/auth/callback?token_hash=synthetic-confirmation-hash&type=email&next=%2Freset-password%3Fsource%3Demail",
    ));
    expect(codeResponse.headers.get("location")).toBe("https://app.example/");
    expect(otpResponse.headers.get("location")).toBe("https://app.example/");
    expect(codeResponse.cookies.get("evidence-layer-recovery")).toBeUndefined();
    expect(otpResponse.cookies.get("evidence-layer-recovery")).toBeUndefined();
  });

  it("rejects duplicate PKCE flow identifiers before exchanging a code", async () => {
    const response = await GET(new Request(
      "https://app.example/auth/callback?code=synthetic-code&sb_flow_id=first&sb_flow_id=second",
    ));
    expect(response.headers.get("location")).toBe("https://app.example/auth/error?reason=invalid-link");
    expect(mocks.exchangeCodeForSession).not.toHaveBeenCalled();
  });

  it("accepts a one-time recovery token hash and rejects malformed token links", async () => {
    const response = await GET(new Request(
      "https://app.example/auth/callback?token_hash=synthetic-hash&type=recovery&next=https%3A%2F%2Foutside.example",
    ));
    expect(response.headers.get("location")).toBe("https://app.example/reset-password");
    expect(mocks.verifyOtp).toHaveBeenCalledWith({ token_hash: "synthetic-hash", type: "recovery" });
    const malformed = await GET(new Request(
      "https://app.example/auth/callback?token_hash=synthetic-hash&type=invite",
    ));
    expect(malformed.headers.get("location")).toBe("https://app.example/auth/error?reason=invalid-link");

    const conflicting = await GET(new Request(
      "https://app.example/auth/callback?code=synthetic-code&token_hash=synthetic-hash&type=recovery",
    ));
    const duplicated = await GET(new Request(
      "https://app.example/auth/callback?code=first&code=second",
    ));
    expect(conflicting.headers.get("location")).toBe("https://app.example/auth/error?reason=invalid-link");
    expect(duplicated.headers.get("location")).toBe("https://app.example/auth/error?reason=invalid-link");
    expect(mocks.exchangeCodeForSession).not.toHaveBeenCalled();
  });

  it("repeats provisioning safely for repeated confirmed callbacks", async () => {
    const first = await GET(new Request(
      "https://app.example/auth/callback?code=first-synthetic-code&next=%2F",
    ));
    const second = await GET(new Request(
      "https://app.example/auth/callback?code=second-synthetic-code&next=%2F",
    ));

    expect(first.headers.get("location")).toBe("https://app.example/");
    expect(second.headers.get("location")).toBe("https://app.example/");
    expect(mocks.provisionPersonalWorkspace).toHaveBeenNthCalledWith(1, "synthetic-session-token");
    expect(mocks.provisionPersonalWorkspace).toHaveBeenNthCalledWith(2, "synthetic-session-token");
  });

  it("does not enter the app when workspace provisioning fails", async () => {
    mocks.provisionPersonalWorkspace.mockResolvedValueOnce(false);
    const response = await GET(new Request(
      "https://app.example/auth/callback?code=synthetic-code&next=%2F",
    ));

    expect(response.headers.get("location")).toBe(
      "https://app.example/auth/error?reason=workspace-provisioning",
    );
    expect(response.headers.get("location")).not.toContain("synthetic-session-token");
  });

  it("does not provision when the exchanged session has no verified user", async () => {
    mocks.getUser.mockResolvedValueOnce({ data: { user: null }, error: { message: "private details" } });
    const response = await GET(new Request(
      "https://app.example/auth/callback?code=synthetic-code&next=%2F",
    ));

    expect(response.headers.get("location")).toBe("https://app.example/auth/error?reason=invalid-link");
    expect(mocks.provisionPersonalWorkspace).not.toHaveBeenCalled();
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
    expect(invalid.headers.get("cache-control")).toBe("no-store");
  });

  it("rejects callbacks with no code and handles missing configuration", async () => {
    const missing = await GET(new Request("https://app.example/auth/callback"));
    expect(missing.headers.get("location")).toBe("https://app.example/auth/error?reason=invalid-link");
    mocks.getPublicConfig.mockReturnValue(null);
    const unconfigured = await GET(new Request("https://app.example/auth/callback?code=synthetic-code"));
    expect(unconfigured.headers.get("location")).toBe("https://app.example/auth/error?reason=configuration");
  });

  it("serves the isolated implicit recovery handoff for the exact reset callback", async () => {
    const response = await GET(new Request("https://app.example/auth/callback?next=%2Freset-password"));
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("content-security-policy")).toContain("script-src 'self'");
    expect(await response.text()).toContain("/recovery-complete.js");
    expect(mocks.exchangeCodeForSession).not.toHaveBeenCalled();

    const extra = await GET(new Request("https://app.example/auth/callback?next=%2Freset-password&next=%2F"));
    expect(extra.headers.get("location")).toBe("https://app.example/auth/error?reason=invalid-link");
  });
});
