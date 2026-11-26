import { getEvidenceServiceBaseUrl } from "./supabase/config";
import { getMfaChallengeStatus } from "./supabase/mfa";
import type { MfaChallengeStatus } from "./supabase/mfa";
import { getVerifiedAuthContext, type AuthenticatedAuthContext } from "./supabase/server";

export type ConnectorProvider = "google_drive" | "notion" | "github";

export async function connectorMfaStatus(auth: AuthenticatedAuthContext): Promise<MfaChallengeStatus> {
  return getMfaChallengeStatus(auth.client);
}

export function connectorMfaRedirectPath(
  provider: ConnectorProvider,
  status: Exclude<MfaChallengeStatus, "not-required">,
): string {
  if (status === "unavailable") return "/auth/error?reason=mfa-unavailable";
  const next = new URLSearchParams({ connector: provider, status: "retry" });
  const mfa = new URLSearchParams({ next: `/workspace?${next.toString()}` });
  return `/mfa?${mfa.toString()}`;
}

export async function connectorMfaFailurePath(
  provider: ConnectorProvider,
  response: Response,
): Promise<string | null> {
  if (response.status !== 403 && response.status !== 503) return null;
  const payload: unknown = await response.clone().json().catch(() => null);
  if (!payload || typeof payload !== "object" || !("code" in payload)) return null;
  if (payload.code === "mfa_required") return connectorMfaRedirectPath(provider, "required");
  if (payload.code === "mfa_unavailable") return connectorMfaRedirectPath(provider, "unavailable");
  return null;
}

async function createConnection(
  auth: AuthenticatedAuthContext,
  path: string,
  body: unknown,
): Promise<Response> {
  const mfaStatus = await connectorMfaStatus(auth);
  if (mfaStatus !== "not-required") {
    const required = mfaStatus === "required";
    return json(required ? 403 : 503, {
      code: required ? "mfa_required" : "mfa_unavailable",
      error: required
        ? "Complete two-factor authentication to continue."
        : "Two-factor authentication status is unavailable.",
    });
  }
  return upstreamJson(path, auth.accessToken, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function json(status: number, payload: unknown): Response {
  return Response.json(payload, {
    status,
    headers: { "Cache-Control": "private, no-store", "Content-Type": "application/json" },
  });
}

function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  try {
    return new URL(origin).origin === new URL(request.url).origin;
  } catch {
    return false;
  }
}

async function session(): Promise<
  | { kind: "ok"; accessToken: string }
  | { kind: "response"; response: Response }
> {
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") {
    return { kind: "response", response: json(401, { error: "Authentication is required." }) };
  }
  if (auth.kind === "unavailable") {
    return { kind: "response", response: json(503, { error: "Identity verification is unavailable." }) };
  }
  const mfaStatus = await getMfaChallengeStatus(auth.client);
  if (mfaStatus === "unavailable") {
    return {
      kind: "response",
      response: json(503, { error: "Two-factor authentication status is unavailable.", code: "mfa_unavailable" }),
    };
  }
  if (mfaStatus === "required") {
    return {
      kind: "response",
      response: json(403, { error: "Complete two-factor authentication to continue.", code: "mfa_required" }),
    };
  }
  return { kind: "ok", accessToken: auth.accessToken };
}

async function upstreamJson(
  path: string,
  accessToken: string,
  init: RequestInit = {},
): Promise<Response> {
  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl) return json(503, { error: "Connector service is unavailable." });
  try {
    const upstream = await fetch(`${serviceUrl}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${accessToken}`,
        ...(init.headers ?? {}),
      },
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(30_000),
    });
    if (upstream.status === 401) return json(401, { error: "Your session is no longer valid." });
    if (upstream.status === 403) return json(403, { error: "Connector is not authorized." });
    if (upstream.status === 404) return json(404, { error: "Connector not found." });
    if (upstream.status === 204) return new Response(null, { status: 204, headers: { "Cache-Control": "private, no-store" } });
    if (upstream.status >= 300) return json(upstream.status === 409 ? 409 : 503, { error: "Connector service is unavailable." });
    const payload = await upstream.json().catch(() => null);
    if (payload === null) return json(502, { error: "Connector service returned an invalid response." });
    return json(upstream.status === 201 ? 201 : 200, payload);
  } catch {
    return json(503, { error: "Connector service is unavailable." });
  }
}

export async function proxyListConnectors(request: Request): Promise<Response> {
  const auth = await session();
  if (auth.kind === "response") return auth.response;
  return upstreamJson("/v1/connectors", auth.accessToken);
}

export async function proxySyncConnector(connectionId: string, request: Request): Promise<Response> {
  if (!/^[0-9a-f-]{36}$/i.test(connectionId)) return json(404, { error: "Connector not found." });
  if (!sameOrigin(request)) return json(403, { error: "Request origin was not accepted." });
  const auth = await session();
  if (auth.kind === "response") return auth.response;
  return upstreamJson(`/v1/connectors/${encodeURIComponent(connectionId)}/sync?limit=25`, auth.accessToken, {
    method: "POST",
  });
}

export async function proxyListNotIndexed(connectionId: string): Promise<Response> {
  if (!/^[0-9a-f-]{36}$/i.test(connectionId)) return json(404, { error: "Connector not found." });
  const auth = await session();
  if (auth.kind === "response") return auth.response;
  return upstreamJson(`/v1/connectors/${encodeURIComponent(connectionId)}/not-indexed`, auth.accessToken);
}

export async function proxyDeleteConnector(connectionId: string, request: Request): Promise<Response> {
  if (!/^[0-9a-f-]{36}$/i.test(connectionId)) return json(404, { error: "Connector not found." });
  if (!sameOrigin(request)) return json(403, { error: "Request origin was not accepted." });
  const auth = await session();
  if (auth.kind === "response") return auth.response;
  return upstreamJson(`/v1/connectors/${encodeURIComponent(connectionId)}`, auth.accessToken, {
    method: "DELETE",
  });
}

export async function createGoogleDriveConnection(
  auth: AuthenticatedAuthContext,
  payload: {
    access_token: string;
    refresh_token: string | null;
    expires_at: string;
    scopes: string[];
    account_email: string | null;
  },
): Promise<Response> {
  return createConnection(auth, "/v1/connectors/google_drive/connection", payload);
}

export async function createNotionConnection(
  auth: AuthenticatedAuthContext,
  payload: {
    access_token: string;
    account_email: string | null;
    scopes: string[];
  },
): Promise<Response> {
  return createConnection(auth, "/v1/connectors/notion/connection", payload);
}

export async function createGitHubConnection(
  auth: AuthenticatedAuthContext,
  installationId: number,
  oauthCode: string,
): Promise<Response> {
  return createConnection(auth, "/v1/connectors/github/connection", {
    installation_id: installationId,
    oauth_code: oauthCode,
  });
}
