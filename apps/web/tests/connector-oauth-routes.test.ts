import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET as githubStart } from "../app/api/connectors/github/start/route";
import { GET as githubCallback } from "../app/api/connectors/github/callback/route";
import { GET as googleDriveStart } from "../app/api/connectors/google-drive/start/route";
import { GET as googleDriveCallback } from "../app/api/connectors/google-drive/callback/route";
import { GET as notionStart } from "../app/api/connectors/notion/start/route";
import { GET as notionCallback } from "../app/api/connectors/notion/callback/route";

const mocks = vi.hoisted(() => ({
  connectorMfaStatus: vi.fn(),
  getVerifiedAuthContext: vi.fn(),
  createGoogleDriveConnection: vi.fn(),
  createNotionConnection: vi.fn(),
  createGitHubConnection: vi.fn(),
  fetch: vi.fn(),
}));

vi.mock("../lib/connector-api", () => ({
  connectorMfaStatus: mocks.connectorMfaStatus,
  connectorMfaRedirectPath: (provider: string) =>
    `/mfa?next=${encodeURIComponent(`/workspace?connector=${provider}&status=retry`)}`,
  connectorMfaFailurePath: vi.fn(),
  createGoogleDriveConnection: mocks.createGoogleDriveConnection,
  createNotionConnection: mocks.createNotionConnection,
  createGitHubConnection: mocks.createGitHubConnection,
}));
vi.mock("../lib/supabase/server", () => ({
  getVerifiedAuthContext: mocks.getVerifiedAuthContext,
}));

const authenticatedContext = {
  kind: "authenticated",
  client: {},
  user: { id: "user-1" },
  accessToken: "synthetic-access-token",
  expiresAt: null,
};

const startRoutes = [
  ["Google Drive", googleDriveStart, "google_drive"],
  ["Notion", notionStart, "notion"],
  ["GitHub", githubStart, "github"],
] as const;

const callbackRoutes = [
  [
    "Google Drive",
    googleDriveCallback,
    "google_drive",
    "https://app.example/api/connectors/google-drive/callback?code=provider-code&state=state",
    "google_drive_oauth_state",
  ],
  [
    "Notion",
    notionCallback,
    "notion",
    "https://app.example/api/connectors/notion/callback?code=provider-code&state=state",
    "notion_oauth_state",
  ],
  [
    "GitHub",
    githubCallback,
    "github",
    "https://app.example/api/connectors/github/callback?installation_id=42&code=install-code&state=state",
    "github_oauth_state",
  ],
] as const;

describe("connector OAuth MFA routes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv("NEXT_PUBLIC_GOOGLE_DRIVE_CONNECTOR_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_NOTION_CONNECTOR_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_GITHUB_CONNECTOR_ENABLED", "true");
    vi.stubEnv("GOOGLE_OAUTH_CLIENT_ID", "google-client");
    vi.stubEnv("NOTION_OAUTH_CLIENT_ID", "notion-client");
    vi.stubEnv("NOTION_REDIRECT_URI", "https://app.example/api/connectors/notion/callback");
    vi.stubEnv("GITHUB_APP_SLUG", "evidence-layer-app");
    mocks.getVerifiedAuthContext.mockResolvedValue(authenticatedContext);
    mocks.connectorMfaStatus.mockResolvedValue("required");
    vi.stubGlobal("fetch", mocks.fetch);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it.each(startRoutes)("requires MFA before starting the %s OAuth flow", async (_name, route, provider) => {
    const response = await route(new Request(`https://app.example/api/connectors/${provider}/start`));
    expect(response.headers.get("location")).toBe(
      `https://app.example/mfa?next=${encodeURIComponent(`/workspace?connector=${provider}&status=retry`)}`,
    );
    expect(mocks.fetch).not.toHaveBeenCalled();
  });

  it.each(callbackRoutes)(
    "requires MFA before exchanging or storing %s OAuth credentials",
    async (_name, route, provider, url, cookieName) => {
      const request = new NextRequest(url, {
        headers: { cookie: `${cookieName}=state` },
      });
      const response = await route(request);
      expect(response.headers.get("location")).toBe(
        `https://app.example/mfa?next=${encodeURIComponent(`/workspace?connector=${provider}&status=retry`)}`,
      );
      expect(mocks.fetch).not.toHaveBeenCalled();
      expect(mocks.createGoogleDriveConnection).not.toHaveBeenCalled();
      expect(mocks.createNotionConnection).not.toHaveBeenCalled();
      expect(mocks.createGitHubConnection).not.toHaveBeenCalled();
    },
  );

  it("rejects a GitHub installation callback without the user authorization code", async () => {
    mocks.connectorMfaStatus.mockResolvedValue("not-required");
    const request = new NextRequest(
      "https://app.example/api/connectors/github/callback?installation_id=42&state=state",
      { headers: { cookie: "github_oauth_state=state" } },
    );
    const response = await githubCallback(request);
    expect(response.headers.get("location")).toBe(
      "https://app.example/workspace?connector=github&status=denied",
    );
    expect(mocks.createGitHubConnection).not.toHaveBeenCalled();
  });

  it("forwards the GitHub authorization code and maps an ownership refusal to denied", async () => {
    mocks.connectorMfaStatus.mockResolvedValue("not-required");
    mocks.createGitHubConnection.mockResolvedValue(
      Response.json({ detail: "GitHub installation is not authorized" }, { status: 403 }),
    );
    const request = new NextRequest(
      "https://app.example/api/connectors/github/callback?installation_id=42&code=install-code&state=state",
      { headers: { cookie: "github_oauth_state=state" } },
    );
    const response = await githubCallback(request);
    expect(mocks.createGitHubConnection).toHaveBeenCalledWith(authenticatedContext, 42, "install-code");
    expect(response.headers.get("location")).toBe(
      "https://app.example/workspace?connector=github&status=denied",
    );
  });
});
