import { getEvidenceServiceBaseUrl } from "./supabase/config";
import { getVerifiedAuthContext } from "./supabase/server";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type WorkspaceSummary = { id: string; name: string; role: "owner" | "member" };

export type WorkspaceEvidenceRecord = {
  id: string;
  workspace_id: string;
  content: string;
  source_label: string | null;
  source_id: string | null;
  source_version_id: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkspaceSourceVersion = {
  source_id: string;
  version_id: string;
  state: "staged" | "published";
  sha256: string;
  size_bytes: number;
  created_at: string;
};

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

function safeLinkedId(value: unknown): string | null | undefined {
  if (value === null) return null;
  if (typeof value === "string" && UUID_PATTERN.test(value)) return value;
  return undefined;
}

function safeEvidenceRecord(value: unknown, workspaceId: string): WorkspaceEvidenceRecord | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const sourceId = safeLinkedId(candidate.source_id);
  const sourceVersionId = safeLinkedId(candidate.source_version_id);
  if (
    typeof candidate.id !== "string" ||
    !UUID_PATTERN.test(candidate.id) ||
    candidate.workspace_id !== workspaceId ||
    typeof candidate.content !== "string" ||
    candidate.content.length > 65_536 ||
    !(candidate.source_label === null ||
      (typeof candidate.source_label === "string" && candidate.source_label.length <= 512)) ||
    sourceId === undefined ||
    sourceVersionId === undefined ||
    (sourceId === null) !== (sourceVersionId === null) ||
    typeof candidate.created_at !== "string" ||
    !Number.isFinite(Date.parse(candidate.created_at)) ||
    typeof candidate.updated_at !== "string" ||
    !Number.isFinite(Date.parse(candidate.updated_at))
  ) {
    return null;
  }
  return {
    id: candidate.id,
    workspace_id: candidate.workspace_id,
    content: candidate.content,
    source_label: candidate.source_label,
    source_id: sourceId,
    source_version_id: sourceVersionId,
    created_at: candidate.created_at,
    updated_at: candidate.updated_at,
  };
}

export type EvidenceMutation = {
  content?: string;
  source_label?: string | null;
  source_id?: string | null;
  source_version_id?: string | null;
};

export async function parseEvidenceMutation(
  request: Request,
  mode: "create" | "update",
): Promise<{ ok: true; value: EvidenceMutation } | { ok: false }> {
  let payload: unknown;
  try {
    const maxRequestBytes = 270_000;
    const declaredLength = Number(request.headers.get("content-length"));
    if (Number.isFinite(declaredLength) && declaredLength > maxRequestBytes) {
      return { ok: false };
    }
    if (!request.body) return { ok: false };

    const reader = request.body.getReader();
    const chunks: Uint8Array[] = [];
    let totalBytes = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > maxRequestBytes) {
        await reader.cancel();
        return { ok: false };
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(totalBytes);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    return { ok: false };
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return { ok: false };
  }

  const input = payload as Record<string, unknown>;
  const keys = Object.keys(input);
  const allowedKeys = new Set(["content", "source_label", "source_id", "source_version_id"]);
  if (keys.some((key) => !allowedKeys.has(key))) {
    return { ok: false };
  }
  if (mode === "create" && typeof input.content !== "string") {
    return { ok: false };
  }
  if (mode === "update" && keys.length === 0) {
    return { ok: false };
  }

  const value: EvidenceMutation = {};
  if (Object.hasOwn(input, "content")) {
    if (typeof input.content !== "string" || !input.content.trim() || input.content.length > 65_536) {
      return { ok: false };
    }
    value.content = input.content.trim();
  }
  if (Object.hasOwn(input, "source_label")) {
    if (input.source_label === null) {
      value.source_label = null;
    } else if (typeof input.source_label === "string" && input.source_label.length <= 512) {
      value.source_label = input.source_label.trim() || null;
    } else {
      return { ok: false };
    }
  }
  const hasSourceId = Object.hasOwn(input, "source_id");
  const hasSourceVersionId = Object.hasOwn(input, "source_version_id");
  // The provenance link is all-or-nothing: setting or clearing it always
  // names both the source and the exact version.
  if (hasSourceId !== hasSourceVersionId) {
    return { ok: false };
  }
  if (hasSourceId) {
    const sourceId = input.source_id;
    const sourceVersionId = input.source_version_id;
    if (sourceId === null && sourceVersionId === null) {
      value.source_id = null;
      value.source_version_id = null;
    } else if (
      typeof sourceId === "string" &&
      UUID_PATTERN.test(sourceId) &&
      typeof sourceVersionId === "string" &&
      UUID_PATTERN.test(sourceVersionId)
    ) {
      value.source_id = sourceId;
      value.source_version_id = sourceVersionId;
    } else {
      return { ok: false };
    }
  }
  return { ok: true, value };
}

type EvidenceRecordsProxyOptions = {
  request: Request;
  workspaceId: string;
  evidenceId?: string;
  method: "GET" | "POST" | "PATCH" | "DELETE";
  body?: EvidenceMutation;
};

export async function proxyWorkspaceEvidenceRequest({
  request,
  workspaceId,
  evidenceId,
  method,
  body,
}: EvidenceRecordsProxyOptions): Promise<Response> {
  if (!isUuid(workspaceId) || (evidenceId !== undefined && !isUuid(evidenceId))) {
    return json(404, { error: "Evidence record not found." });
  }
  if (method !== "GET" && !sameOrigin(request)) {
    return json(403, { error: "Request origin was not accepted." });
  }

  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") return json(401, { error: "Authentication is required." });
  if (auth.kind === "unavailable") return json(503, { error: "Identity verification is unavailable." });

  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl) return json(503, { error: "Evidence service is unavailable." });

  const path = `/v1/workspaces/${workspaceId}/evidence${evidenceId ? `/${evidenceId}` : ""}`;
  let upstream: Response;
  try {
    upstream = await fetch(serviceUrl + path, {
      method,
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${auth.accessToken}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    return json(503, { error: "Evidence service is unavailable." });
  }

  if (upstream.status === 401) return json(401, { error: "Your session is no longer valid." });
  if (upstream.status === 403) return json(403, { error: "This action is not available for this account." });
  if (upstream.status === 404) return json(404, { error: "Evidence record not found." });
  if (upstream.status === 422) return json(400, { error: "Evidence input was not accepted." });
  if (upstream.status >= 500 || upstream.status >= 300) {
    return json(503, { error: "Evidence service is unavailable." });
  }
  const expectedStatus = method === "POST" ? 201 : method === "DELETE" ? 204 : 200;
  if (upstream.status !== expectedStatus) {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  if (method === "DELETE") {
    return new Response(null, { status: 204, headers: { "Cache-Control": "no-store" } });
  }

  let payload: unknown;
  try {
    payload = await upstream.json();
  } catch {
    return json(502, { error: "Evidence service returned an invalid response." });
  }

  if (!evidenceId && method === "GET") {
    if (!Array.isArray(payload)) return json(502, { error: "Evidence service returned an invalid response." });
    const records = payload.map((record) => safeEvidenceRecord(record, workspaceId));
    return records.some((record) => record === null)
      ? json(502, { error: "Evidence service returned an invalid response." })
      : json(200, records);
  }

  const record = safeEvidenceRecord(payload, workspaceId);
  return record && (evidenceId === undefined || record.id === evidenceId)
    ? json(200, record)
    : json(502, { error: "Evidence service returned an invalid response." });
}

function safeSourceVersion(value: unknown): WorkspaceSourceVersion | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.source_id !== "string" ||
    !UUID_PATTERN.test(candidate.source_id) ||
    typeof candidate.version_id !== "string" ||
    !UUID_PATTERN.test(candidate.version_id) ||
    (candidate.state !== "staged" && candidate.state !== "published") ||
    typeof candidate.sha256 !== "string" ||
    !/^[0-9a-f]{64}$/.test(candidate.sha256) ||
    typeof candidate.size_bytes !== "number" ||
    !Number.isSafeInteger(candidate.size_bytes) ||
    candidate.size_bytes < 0 ||
    candidate.size_bytes > 8 * 1024 * 1024 ||
    typeof candidate.created_at !== "string" ||
    !Number.isFinite(Date.parse(candidate.created_at))
  ) {
    return null;
  }
  return {
    source_id: candidate.source_id,
    version_id: candidate.version_id,
    state: candidate.state,
    sha256: candidate.sha256,
    size_bytes: candidate.size_bytes,
    created_at: candidate.created_at,
  };
}

export async function proxyWorkspaceSourcesRequest({
  workspaceId,
}: {
  workspaceId: string;
}): Promise<Response> {
  if (!isUuid(workspaceId)) {
    return json(404, { error: "Workspace not found." });
  }

  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") return json(401, { error: "Authentication is required." });
  if (auth.kind === "unavailable") return json(503, { error: "Identity verification is unavailable." });

  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl) return json(503, { error: "Evidence service is unavailable." });

  let upstream: Response;
  try {
    upstream = await fetch(`${serviceUrl}/v1/workspaces/${workspaceId}/sources`, {
      method: "GET",
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
  if (upstream.status !== 200) return json(503, { error: "Evidence service is unavailable." });

  let payload: unknown;
  try {
    payload = await upstream.json();
  } catch {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  if (!Array.isArray(payload)) {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  const versions = payload.map(safeSourceVersion);
  return versions.some((version) => version === null)
    ? json(502, { error: "Evidence service returned an invalid response." })
    : json(200, versions);
}

type SourceContentProxyOptions = {
  workspaceId: string;
  sourceId: string;
  versionId: string;
};

export async function proxyVerifiedSourceContent({
  workspaceId,
  sourceId,
  versionId,
}: SourceContentProxyOptions): Promise<Response> {
  if (![workspaceId, sourceId, versionId].every(isUuid)) {
    return json(404, { error: "Source version not found." });
  }
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") return json(401, { error: "Authentication is required." });
  if (auth.kind === "unavailable") return json(503, { error: "Identity verification is unavailable." });

  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl) return json(503, { error: "Evidence service is unavailable." });
  const endpoint = `/v1/workspaces/${workspaceId}/sources/${sourceId}/versions/${versionId}/content`;

  let upstream: Response;
  try {
    upstream = await fetch(serviceUrl + endpoint, {
      method: "GET",
      headers: { Accept: "application/octet-stream", Authorization: `Bearer ${auth.accessToken}` },
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(15_000),
    });
  } catch {
    return json(503, { error: "Verified source content is unavailable." });
  }
  if (upstream.status === 401) return json(401, { error: "Your session is no longer valid." });
  if (upstream.status === 403) return json(403, { error: "Source read is not authorized." });
  if (upstream.status === 404) return json(404, { error: "Source version not found." });
  if (upstream.status >= 300) return json(503, { error: "Verified source content is unavailable." });
  if (upstream.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/octet-stream") {
    return json(502, { error: "Evidence service returned an invalid response." });
  }

  const declaredLength = Number(upstream.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > 4 * 1024 * 1024) {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  if (!upstream.body) {
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
      if (totalBytes > 4 * 1024 * 1024) {
        await reader.cancel();
        return json(502, { error: "Evidence service returned an invalid response." });
      }
      chunks.push(value);
    }
  } catch {
    return json(503, { error: "Verified source content is unavailable." });
  }
  const bytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  const digest = upstream.headers.get("content-digest");
  const headers = new Headers({
    "Cache-Control": "no-store",
    "Content-Type": "application/octet-stream",
    "X-Content-Type-Options": "nosniff",
  });
  if (digest && /^sha-256=:[A-Za-z0-9+/]+=*:$/.test(digest)) {
    headers.set("Content-Digest", digest);
  }
  return new Response(bytes, { status: 200, headers });
}
