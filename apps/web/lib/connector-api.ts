import { getEvidenceServiceBaseUrl } from "./supabase/config";
import { mfaChallengeRequired } from "./supabase/mfa";
import { getVerifiedAuthContext } from "./supabase/server";

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
  if (await mfaChallengeRequired(auth.client)) {
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
  return upstreamJson(`/v1/connectors/${encodeURIComponent(connectionId)}/sync?limit=10`, auth.accessToken, {
    method: "POST",
  });
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
  accessToken: string,
  payload: {
    access_token: string;
    refresh_token: string | null;
    expires_at: string;
    scopes: string[];
    account_email: string | null;
  },
): Promise<Response> {
  return upstreamJson("/v1/connectors/google_drive/connection", accessToken, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function createNotionConnection(
  accessToken: string,
  payload: {
    access_token: string;
    account_email: string | null;
    scopes: string[];
  },
): Promise<Response> {
  return upstreamJson("/v1/connectors/notion/connection", accessToken, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
