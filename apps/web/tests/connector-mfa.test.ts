import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthenticatedAuthContext } from "../lib/supabase/server";
import {
  connectorMfaFailurePath,
  connectorMfaRedirectPath,
  connectorMfaStatus,
  createGitHubConnection,
} from "../lib/connector-api";

const mocks = vi.hoisted(() => ({
  getMfaChallengeStatus: vi.fn(),
  fetch: vi.fn(),
}));

vi.mock("../lib/supabase/mfa", () => ({
  getMfaChallengeStatus: mocks.getMfaChallengeStatus,
}));
vi.mock("../lib/supabase/config", () => ({
  getEvidenceServiceBaseUrl: () => "https://evidence.example",
}));
vi.mock("../lib/supabase/server", () => ({
  getVerifiedAuthContext: vi.fn(),
}));

const auth = {
  kind: "authenticated",
  client: {},
  user: { id: "user-1" },
  accessToken: "synthetic-access-token",
  expiresAt: null,
} as AuthenticatedAuthContext;

describe("connector OAuth MFA gate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("fetch", mocks.fetch);
  });

  it("returns an MFA challenge destination that resumes at the connector panel", () => {
    expect(connectorMfaRedirectPath("google_drive", "required")).toBe(
      "/mfa?next=%2Fworkspace%3Fconnector%3Dgoogle_drive%26status%3Dretry",
    );
    expect(connectorMfaRedirectPath("notion", "unavailable")).toBe(
      "/auth/error?reason=mfa-unavailable",
    );
  });

  it("reads the MFA requirement from the verified server session", async () => {
    mocks.getMfaChallengeStatus.mockResolvedValue("required");
    expect(await connectorMfaStatus(auth)).toBe("required");
    expect(mocks.getMfaChallengeStatus).toHaveBeenCalledWith(auth.client);
  });

  it("blocks connection creation at the shared boundary when MFA is required", async () => {
    mocks.getMfaChallengeStatus.mockResolvedValue("required");
    const response = await createGitHubConnection(auth, 42);

    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ code: "mfa_required" });
    expect(mocks.fetch).not.toHaveBeenCalled();
  });

  it("only forwards connector credentials after MFA status is known to be clear", async () => {
    mocks.getMfaChallengeStatus.mockResolvedValue("not-required");
    mocks.fetch.mockResolvedValue(Response.json({ connection_id: "connection-1" }, { status: 201 }));

    const response = await createGitHubConnection(auth, 42);

    expect(response.status).toBe(201);
    expect(mocks.fetch).toHaveBeenCalledWith(
      "https://evidence.example/v1/connectors/github/connection",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer synthetic-access-token" }),
        body: JSON.stringify({ installation_id: 42 }),
      }),
    );
  });

  it("maps a late MFA denial to a useful challenge destination", async () => {
    const response = Response.json({ code: "mfa_required" }, { status: 403 });
    expect(await connectorMfaFailurePath("github", response)).toBe(
      "/mfa?next=%2Fworkspace%3Fconnector%3Dgithub%26status%3Dretry",
    );
    expect(await connectorMfaFailurePath("github", Response.json({ error: "upstream" }, { status: 503 }))).toBeNull();
  });
});
