import { getEvidenceServiceBaseUrl } from "./supabase/config";
import { getVerifiedAuthContext } from "./supabase/server";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type WorkspaceSummary = { id: string; name: string; role: "owner" | "member" };

function json(status: number, payload: unknown): Response {
  return Response.json(payload, {
    status,
    headers: { "Cache-Control": "no-store", "Content-Type": "application/json" },
  });
}

function safeWorkspace(value: unknown): WorkspaceSummary | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.id !== "string" ||
    !UUID_PATTERN.test(candidate.id) ||
    typeof candidate.name !== "string" ||
    candidate.name.length > 160 ||
    (candidate.role !== "owner" && candidate.role !== "member")
  ) {
    return null;
  }
  return { id: candidate.id, name: candidate.name, role: candidate.role };
}

export async function provisionPersonalWorkspaceForSession(accessToken: string): Promise<boolean> {
  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl || !accessToken.trim()) return false;

  try {
    const response = await fetch(`${serviceUrl}/v1/me/personal-workspace`, {
      method: "PUT",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${accessToken}`,
      },
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(10_000),
    });
    if (response.status !== 200) return false;

    const workspace = safeWorkspace(await response.json());
    return workspace?.role === "owner";
  } catch {
    return false;
  }
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

type ProxyOptions = {
  endpoint: string;
  method: "GET" | "PUT";
  request: Request;
  requireSameOrigin?: boolean;
  responseKind: "me" | "workspace";
};

export async function proxyEvidenceRequest({
  endpoint,
  method,
  request,
  requireSameOrigin = false,
  responseKind,
}: ProxyOptions): Promise<Response> {
  if (requireSameOrigin && !sameOrigin(request)) {
    return json(403, { error: "Request origin was not accepted." });
  }

  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") {
    return json(401, { error: "Authentication is required." });
  }
  if (auth.kind === "unavailable") {
    return json(503, { error: "Identity verification is unavailable." });
  }

  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl) return json(503, { error: "Evidence service is unavailable." });

  let upstream: Response;
  try {
    upstream = await fetch(`${serviceUrl}${endpoint}`, {
      method,
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${auth.accessToken}`,
      },
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    return json(503, { error: "Evidence service is unavailable." });
  }

  if (upstream.status === 401) return json(401, { error: "Your session is no longer valid." });
  if (upstream.status === 403) return json(403, { error: "This action is not available for this account." });
  if (upstream.status === 404) return json(404, { error: "Workspace not found." });
  if (upstream.status >= 500 || upstream.status >= 300) {
    return json(503, { error: "Evidence service is unavailable." });
  }
  if (upstream.status !== 200) return json(503, { error: "Evidence service is unavailable." });

  let payload: unknown;
  try {
    payload = await upstream.json();
  } catch (error) {
    if (
      (error instanceof Error || error instanceof DOMException) &&
      (error.name === "AbortError" || error.name === "TimeoutError")
    ) {
      return json(503, { error: "Evidence service is unavailable." });
    }
    return json(502, { error: "Evidence service returned an invalid response." });
  }

  if (responseKind === "workspace") {
    const workspace = safeWorkspace(payload);
    return workspace
      ? json(200, workspace)
      : json(502, { error: "Evidence service returned an invalid response." });
  }

  if (!payload || typeof payload !== "object") {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  const candidate = payload as Record<string, unknown>;
  if (
    candidate.user_id !== auth.user.id ||
    !Array.isArray(candidate.workspaces)
  ) {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  const workspaces = candidate.workspaces.map(safeWorkspace);
  if (workspaces.some((workspace) => workspace === null)) {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  return json(200, { user_id: auth.user.id, workspaces });
}

export function isUuid(value: string): boolean {
  return UUID_PATTERN.test(value);
}
