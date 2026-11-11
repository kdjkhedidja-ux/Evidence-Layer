import { getEvidenceServiceBaseUrl } from "./supabase/config";
import { mfaChallengeRequired } from "./supabase/mfa";
import { getVerifiedAuthContext } from "./supabase/server";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_EXPORT_BYTES = 8 * 1024 * 1024;

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

async function verifiedSession(): Promise<
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
      response: json(403, {
        error: "Complete two-factor authentication to continue.",
        code: "mfa_required",
      }),
    };
  }
  return { kind: "ok", accessToken: auth.accessToken };
}

export async function proxyCreateAccountExport(request: Request): Promise<Response> {
  if (!sameOrigin(request)) return json(403, { error: "Request origin was not accepted." });
  const session = await verifiedSession();
  if (session.kind === "response") return session.response;
  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl) return json(503, { error: "Evidence service is unavailable." });

  let value: unknown;
  try {
    value = await request.json();
  } catch {
    return json(400, { error: "An idempotency key is required." });
  }
  if (!value || typeof value !== "object") {
    return json(400, { error: "An idempotency key is required." });
  }
  const key = (value as Record<string, unknown>).idempotency_key;
  if (typeof key !== "string" || key.trim().length < 1 || key.length > 128) {
    return json(400, { error: "An idempotency key is required." });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${serviceUrl}/v1/me/exports`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${session.accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ idempotency_key: key.trim() }),
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(30_000),
    });
  } catch {
    return json(503, { error: "Export service is unavailable." });
  }
  if (upstream.status === 401) return json(401, { error: "Your session is no longer valid." });
  if (upstream.status === 403) return json(403, { error: "Export is not available for this account." });
  if (upstream.status === 413) return json(413, { error: "Export exceeds the narrowed size limit." });
  if (upstream.status >= 300) return json(503, { error: "Export service is unavailable." });
  const payload = await upstream.json().catch(() => null);
  if (!payload || typeof payload !== "object") {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  return json(201, payload);
}

export async function proxyDownloadAccountExport(exportId: string, request: Request): Promise<Response> {
  if (!UUID_PATTERN.test(exportId)) return json(404, { error: "Export not found." });
  const session = await verifiedSession();
  if (session.kind === "response") return session.response;
  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl) return json(503, { error: "Evidence service is unavailable." });
  const grant = request.headers.get("x-export-grant");
  if (!grant || grant.length > 128) return json(404, { error: "Export not found." });

  let upstream: Response;
  try {
    upstream = await fetch(`${serviceUrl}/v1/me/exports/${exportId}/download`, {
      method: "GET",
      headers: {
        Accept: "application/zip",
        Authorization: `Bearer ${session.accessToken}`,
        "X-Export-Grant": grant,
      },
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(30_000),
    });
  } catch {
    return json(503, { error: "Export download is unavailable." });
  }
  if (upstream.status === 401) return json(401, { error: "Your session is no longer valid." });
  if (upstream.status === 403) return json(403, { error: "Export is not authorized." });
  if (upstream.status === 404) return json(404, { error: "Export not found." });
  if (upstream.status >= 300) return json(503, { error: "Export download is unavailable." });
  if (upstream.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/zip") {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  if (!upstream.body) return json(502, { error: "Evidence service returned an invalid response." });

  const declaredLength = Number(upstream.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_EXPORT_BYTES) {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  const reader = upstream.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > MAX_EXPORT_BYTES) {
        await reader.cancel();
        return json(502, { error: "Evidence service returned an invalid response." });
      }
      chunks.push(value);
    }
  } catch {
    return json(503, { error: "Export download is unavailable." });
  }
  const bytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  const headers = new Headers({
    "Cache-Control": "private, no-store",
    "Content-Type": "application/zip",
    "Content-Disposition": `attachment; filename="evidence-layer-export-${exportId}.zip"`,
    "X-Content-Type-Options": "nosniff",
  });
  const digest = upstream.headers.get("content-digest");
  if (digest && /^sha-256=:[A-Za-z0-9+/]+=*:$/.test(digest)) {
    headers.set("Content-Digest", digest);
  }
  return new Response(bytes, { status: 200, headers });
}

export async function proxyRevokeAccountExport(exportId: string, request: Request): Promise<Response> {
  if (!UUID_PATTERN.test(exportId)) return json(404, { error: "Export not found." });
  if (!sameOrigin(request)) return json(403, { error: "Request origin was not accepted." });
  const session = await verifiedSession();
  if (session.kind === "response") return session.response;
  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl) return json(503, { error: "Evidence service is unavailable." });
  let upstream: Response;
  try {
    upstream = await fetch(`${serviceUrl}/v1/me/exports/${exportId}/revoke`, {
      method: "POST",
      headers: { Accept: "application/json", Authorization: `Bearer ${session.accessToken}` },
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    return json(503, { error: "Export service is unavailable." });
  }
  if (upstream.status === 401) return json(401, { error: "Your session is no longer valid." });
  if (upstream.status === 403) return json(403, { error: "Export is not authorized." });
  if (upstream.status >= 300) return json(503, { error: "Export service is unavailable." });
  const payload = await upstream.json().catch(() => null);
  if (!payload || typeof payload !== "object") {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  return json(200, payload);
}
