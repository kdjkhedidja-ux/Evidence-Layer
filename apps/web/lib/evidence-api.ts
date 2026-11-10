import { getEvidenceServiceBaseUrl } from "./supabase/config";
import { mfaChallengeRequired } from "./supabase/mfa";
import { getVerifiedAuthContext } from "./supabase/server";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type WorkspaceSummary = { id: string; name: string; role: "owner" | "admin" | "member"; workspace_type?: "personal" | "group" };
export type AccountType = "individual" | "organization";
export type AccountSummary = {
  user_id: string;
  account_type: AccountType;
  organization_name: string | null;
  can_manage_workspace: boolean;
  workspaces: WorkspaceSummary[];
};

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

export type WorkspaceEvidenceRelationship = {
  id: string;
  workspace_id: string;
  evidence_id_a: string;
  evidence_id_b: string;
  relation_type: "supports" | "contradicts" | "contextualizes" | "derived_from";
  rationale: string;
  provenance_note: string;
  created_by: string;
  created_at: string;
  updated_by: string;
  updated_at: string;
};

export type PersonalApiKeySummary = {
  id: string;
  workspace_id: string;
  name: string;
  key_prefix: string;
  scopes: Array<"evidence:read" | "relationships:read">;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
};

export type PersonalApiKeyCreated = PersonalApiKeySummary & { token: string };

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
    (candidate.role !== "owner" && candidate.role !== "admin" && candidate.role !== "member")
  ) {
    return null;
  }
  const workspaceType =
    candidate.workspace_type === "group"
      ? "group"
      : candidate.workspace_type === "personal"
        ? "personal"
        : undefined;
  return {
    id: candidate.id,
    name: candidate.name,
    role: candidate.role,
    ...(workspaceType ? { workspace_type: workspaceType } : {}),
  };
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

async function mfaBlocked(
  auth: Awaited<ReturnType<typeof getVerifiedAuthContext>>,
): Promise<Response | null> {
  if (auth.kind !== "authenticated") return null;
  if (await mfaChallengeRequired(auth.client)) {
    return json(403, {
      error: "Complete two-factor authentication to continue.",
      code: "mfa_required",
    });
  }
  return null;
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
  const blocked = await mfaBlocked(auth);
  if (blocked) return blocked;

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

  const account = safeMe(payload, auth.user.id);
  return account
    ? json(200, account)
    : json(502, { error: "Evidence service returned an invalid response." });
}

type PersonalApiKeyProxyOptions = {
  request: Request;
  method: "GET" | "POST" | "DELETE";
  keyId?: string;
};

export async function proxyPersonalApiKeyRequest({
  request,
  method,
  keyId,
}: PersonalApiKeyProxyOptions): Promise<Response> {
  if (method !== "GET" && !sameOrigin(request)) {
    return json(403, { error: "Request origin was not accepted." });
  }

  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") return json(401, { error: "Authentication is required." });
  if (auth.kind === "unavailable") return json(503, { error: "Identity verification is unavailable." });
  const blocked = await mfaBlocked(auth);
  if (blocked) return blocked;

  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl) return json(503, { error: "Evidence service is unavailable." });

  let endpoint = "/v1/me/api-keys";
  let body: Record<string, unknown> | undefined;
  if (method === "POST") {
    let value: unknown;
    try {
      value = await request.json();
    } catch {
      return json(400, { error: "Invalid API key request." });
    }
    if (!value || typeof value !== "object") return json(400, { error: "Invalid API key request." });
    const candidate = value as Record<string, unknown>;
    const name = typeof candidate.name === "string" ? candidate.name.trim() : "";
    if (
      typeof candidate.workspace_id !== "string" ||
      !UUID_PATTERN.test(candidate.workspace_id) ||
      !name ||
      name.length > 80 ||
      !Number.isInteger(candidate.expiration_days) ||
      Number(candidate.expiration_days) < 1 ||
      Number(candidate.expiration_days) > 90
    ) {
      return json(400, { error: "Choose a workspace, a key name, and an expiry from 1 to 90 days." });
    }
    body = {
      workspace_id: candidate.workspace_id,
      name,
      expiration_days: candidate.expiration_days,
    };
  }
  if (method === "DELETE") {
    if (!keyId || !UUID_PATTERN.test(keyId)) return json(400, { error: "Invalid API key id." });
    endpoint = `/v1/me/api-keys/${keyId}`;
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${serviceUrl}${endpoint}`, {
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

  const safeErrors: Record<number, string> = {
    401: "Your session is no longer valid.",
    403: "This API key action is not available for this account.",
    404: "API key not found.",
    409: "The active API key limit was reached.",
    422: "The API key request is invalid.",
    429: "The request limit was reached. Try again shortly.",
  };
  if (upstream.status in safeErrors) return json(upstream.status, { error: safeErrors[upstream.status] });
  if (upstream.status >= 500 || upstream.status >= 300) {
    return json(503, { error: "Evidence service is unavailable." });
  }

  let payload: unknown;
  try {
    payload = await upstream.json();
  } catch {
    return json(502, { error: "Evidence service returned an invalid response." });
  }

  if (method === "GET") {
    if (!Array.isArray(payload)) return json(502, { error: "Evidence service returned an invalid response." });
    const keys = payload.map((value) => safePersonalApiKey(value));
    if (keys.some((value) => value === null)) return json(502, { error: "Evidence service returned an invalid response." });
    return json(upstream.status, keys);
  }
  if (method === "DELETE") {
    if (!payload || typeof payload !== "object") return json(502, { error: "Evidence service returned an invalid response." });
    const result = payload as Record<string, unknown>;
    if (result.id !== keyId || result.revoked !== true) return json(502, { error: "Evidence service returned an invalid response." });
    return json(upstream.status, { id: keyId, revoked: true });
  }

  const created = safePersonalApiKey(payload, true);
  return created && "token" in created
    ? json(upstream.status, created)
    : json(502, { error: "Evidence service returned an invalid response." });
}

function safePersonalApiKey(value: unknown, allowToken = false): PersonalApiKeySummary | PersonalApiKeyCreated | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const scopes = candidate.scopes;
  const validScopes =
    Array.isArray(scopes) &&
    scopes.length === 2 &&
    scopes.includes("evidence:read") &&
    scopes.includes("relationships:read");
  const timestamps = [candidate.created_at, candidate.expires_at];
  const optionalTimestamps = [candidate.revoked_at, candidate.last_used_at];
  if (
    typeof candidate.id !== "string" || !UUID_PATTERN.test(candidate.id) ||
    typeof candidate.workspace_id !== "string" || !UUID_PATTERN.test(candidate.workspace_id) ||
    typeof candidate.name !== "string" || !candidate.name.trim() || candidate.name.length > 80 ||
    typeof candidate.key_prefix !== "string" || !/^evl_pk_[0-9a-f]{8}$/.test(candidate.key_prefix) ||
    !validScopes ||
    timestamps.some((timestamp) => typeof timestamp !== "string" || !Number.isFinite(Date.parse(timestamp))) ||
    optionalTimestamps.some((timestamp) => timestamp !== null && (typeof timestamp !== "string" || !Number.isFinite(Date.parse(timestamp))))
  ) {
    return null;
  }
  const base: PersonalApiKeySummary = {
    id: candidate.id,
    workspace_id: candidate.workspace_id,
    name: candidate.name,
    key_prefix: candidate.key_prefix,
    scopes: scopes as PersonalApiKeySummary["scopes"],
    created_at: candidate.created_at as string,
    expires_at: candidate.expires_at as string,
    revoked_at: candidate.revoked_at as string | null,
    last_used_at: candidate.last_used_at as string | null,
  };
  if (!allowToken) return base;
  const tokenPattern = new RegExp(`^evl_pk_${candidate.id}\\.[A-Za-z0-9_-]{43}$`);
  return typeof candidate.token === "string" && tokenPattern.test(candidate.token)
    ? { ...base, token: candidate.token }
    : null;
}

function safeMe(value: unknown, userId: string): AccountSummary | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  if (candidate.user_id !== userId || !Array.isArray(candidate.workspaces)) {
    return null;
  }
  const workspaces = candidate.workspaces.map(safeWorkspace);
  if (workspaces.some((workspace) => workspace === null)) return null;
  const accountType: AccountType =
    candidate.account_type === "organization" ? "organization" : "individual";
  const organizationName =
    typeof candidate.organization_name === "string" && candidate.organization_name.trim()
      ? candidate.organization_name.trim()
      : null;
  if (accountType === "organization" && !organizationName) return null;
  return {
    user_id: userId,
    account_type: accountType,
    organization_name: organizationName,
    can_manage_workspace: accountType === "organization",
    workspaces: workspaces as WorkspaceSummary[],
  };
}

export async function proxyCreateOrganization(request: Request): Promise<Response> {
  if (!sameOrigin(request)) {
    return json(403, { error: "Request origin was not accepted." });
  }
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") {
    return json(401, { error: "Authentication is required." });
  }
  if (auth.kind === "unavailable") {
    return json(503, { error: "Identity verification is unavailable." });
  }
  const blocked = await mfaBlocked(auth);
  if (blocked) return blocked;
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return json(400, { error: "An organization name is required." });
  }
  if (!payload || typeof payload !== "object") {
    return json(400, { error: "An organization name is required." });
  }
  const name = (payload as Record<string, unknown>).name;
  if (typeof name !== "string" || name.trim().length < 1 || name.trim().length > 120) {
    return json(400, { error: "Organization name must be 1-120 characters." });
  }
  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl) return json(503, { error: "Evidence service is unavailable." });
  let upstream: Response;
  try {
    upstream = await fetch(`${serviceUrl}/v1/me/organization`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${auth.accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ name: name.trim() }),
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    return json(503, { error: "Evidence service is unavailable." });
  }
  if (upstream.status === 401) return json(401, { error: "Your session is no longer valid." });
  if (upstream.status === 403) return json(403, { error: "This action is not available for this account." });
  if (upstream.status !== 200 && upstream.status !== 201) {
    return json(503, { error: "Organization account service is unavailable." });
  }
  let responsePayload: unknown;
  try {
    responsePayload = await upstream.json();
  } catch {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  const account = safeMe(responsePayload, auth.user.id);
  return account
    ? json(200, account)
    : json(502, { error: "Evidence service returned an invalid response." });
}

export type OrganizationMember = {
  user_id: string;
  email: string;
  role: "admin" | "member";
};
export type OrganizationWorkspace = { id: string; name: string };
export type OrganizationOverview = {
  id: string;
  name: string;
  owner_user_id: string;
  members: OrganizationMember[];
  workspaces: OrganizationWorkspace[];
};
export type GroupWorkspaceCreated = { workspace_id: string; name: string };
export type OrganizationMemberRole = { user_id: string; role: "admin" | "member" };
export type OrganizationMemberRemoval = { user_id: string; removed: boolean };

function safeOrganizationMember(value: unknown): OrganizationMember | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.user_id !== "string" ||
    !UUID_PATTERN.test(candidate.user_id) ||
    typeof candidate.email !== "string" ||
    !candidate.email.includes("@") ||
    (candidate.role !== "admin" && candidate.role !== "member")
  ) return null;
  return {
    user_id: candidate.user_id,
    email: candidate.email,
    role: candidate.role,
  };
}

function safeOrganizationWorkspace(value: unknown): OrganizationWorkspace | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.id !== "string" ||
    !UUID_PATTERN.test(candidate.id) ||
    typeof candidate.name !== "string" ||
    candidate.name.length < 1 ||
    candidate.name.length > 120
  ) return null;
  return { id: candidate.id, name: candidate.name };
}

function safeOrganization(value: unknown): OrganizationOverview | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.id !== "string" ||
    !UUID_PATTERN.test(candidate.id) ||
    typeof candidate.name !== "string" ||
    candidate.name.length < 1 ||
    typeof candidate.owner_user_id !== "string" ||
    !UUID_PATTERN.test(candidate.owner_user_id) ||
    !Array.isArray(candidate.members) ||
    !Array.isArray(candidate.workspaces)
  ) return null;
  const members = candidate.members.map(safeOrganizationMember);
  const workspaces = candidate.workspaces.map(safeOrganizationWorkspace);
  if (members.some((member) => member === null) || workspaces.some((workspace) => workspace === null)) {
    return null;
  }
  return {
    id: candidate.id,
    name: candidate.name,
    owner_user_id: candidate.owner_user_id,
    members: members as OrganizationMember[],
    workspaces: workspaces as OrganizationWorkspace[],
  };
}

type OrganizationResponseKind = "overview" | "workspace" | "workspaceDelete" | "member" | "memberRole" | "memberRemoval";

type OrganizationProxyOptions = {
  endpoint: string;
  method: "GET" | "POST" | "PATCH" | "DELETE";
  request: Request;
  body?: unknown;
  responseKind: OrganizationResponseKind;
};

export async function proxyOrganizationRequest({
  endpoint,
  method,
  request,
  body,
  responseKind,
}: OrganizationProxyOptions): Promise<Response> {
  if (method !== "GET" && !sameOrigin(request)) {
    return json(403, { error: "Request origin was not accepted." });
  }
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") {
    return json(401, { error: "Authentication is required." });
  }
  if (auth.kind === "unavailable") {
    return json(503, { error: "Identity verification is unavailable." });
  }
  const blocked = await mfaBlocked(auth);
  if (blocked) return blocked;
  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl) return json(503, { error: "Evidence service is unavailable." });

  let upstream: Response;
  try {
    upstream = await fetch(`${serviceUrl}${endpoint}`, {
      method,
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${auth.accessToken}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    return json(503, { error: "Evidence service is unavailable." });
  }
  if (upstream.status === 401) return json(401, { error: "Your session is no longer valid." });
  if (upstream.status === 403) return json(403, { error: "Organization admin access is required." });
  if (upstream.status === 404) return json(404, { error: "Organization member not found." });
  if (upstream.status === 409) return json(409, { error: "The organization change conflicts with current state." });
  if (upstream.status === 400 || upstream.status === 422) {
    return json(422, { error: "The organization change was not accepted." });
  }
  if (upstream.status !== 200 && upstream.status !== 201) {
    return json(503, { error: "Organization service is unavailable." });
  }
  let payload: unknown;
  try {
    payload = await upstream.json();
  } catch {
    return json(502, { error: "Evidence service returned an invalid response." });
  }

  let parsed: unknown = null;
  if (responseKind === "overview") {
    parsed = safeOrganization(payload);
  } else if (responseKind === "workspace") {
    const candidate = payload as Record<string, unknown>;
    if (
      candidate &&
      typeof candidate.workspace_id === "string" &&
      UUID_PATTERN.test(candidate.workspace_id) &&
      typeof candidate.name === "string"
    ) parsed = { workspace_id: candidate.workspace_id, name: candidate.name };
  } else if (responseKind === "workspaceDelete") {
    const candidate = payload as Record<string, unknown>;
    if (
      candidate &&
      typeof candidate.workspace_id === "string" &&
      UUID_PATTERN.test(candidate.workspace_id) &&
      candidate.workspace_deleted === true &&
      typeof candidate.snapshots_deleted === "number" &&
      typeof candidate.storage_cleanup_queued === "number"
    ) {
      parsed = {
        workspace_id: candidate.workspace_id,
        workspace_deleted: true,
        snapshots_deleted: candidate.snapshots_deleted,
        storage_cleanup_queued: candidate.storage_cleanup_queued,
      };
    }
  } else if (responseKind === "member") {
    parsed = safeOrganizationMember(payload);
  } else if (responseKind === "memberRole") {
    const candidate = payload as Record<string, unknown>;
    if (
      candidate &&
      typeof candidate.user_id === "string" &&
      UUID_PATTERN.test(candidate.user_id) &&
      (candidate.role === "admin" || candidate.role === "member")
    ) parsed = { user_id: candidate.user_id, role: candidate.role };
  } else if (responseKind === "memberRemoval") {
    const candidate = payload as Record<string, unknown>;
    if (
      candidate &&
      typeof candidate.user_id === "string" &&
      UUID_PATTERN.test(candidate.user_id) &&
      candidate.removed === true
    ) parsed = { user_id: candidate.user_id, removed: true };
  }
  return parsed
    ? json(upstream.status, parsed)
    : json(502, { error: "Evidence service returned an invalid response." });
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
  const blocked = await mfaBlocked(auth);
  if (blocked) return blocked;

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

export type RelationshipMutation = {
  evidence_id_a?: string;
  evidence_id_b?: string;
  relation_type?: WorkspaceEvidenceRelationship["relation_type"];
  rationale?: string;
  provenance_note?: string;
};

export async function parseRelationshipMutation(
  request: Request,
  mode: "create" | "update",
): Promise<{ ok: true; value: RelationshipMutation } | { ok: false }> {
  let payload: unknown;
  try {
    const maxRequestBytes = 16_384;
    const length = Number(request.headers.get("content-length"));
    if (Number.isFinite(length) && length > maxRequestBytes) return { ok: false };
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
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return { ok: false };

  const input = payload as Record<string, unknown>;
  const keys = Object.keys(input);
  const allowed = new Set(["evidence_id_a", "evidence_id_b", "relation_type", "rationale", "provenance_note"]);
  if (keys.some((key) => !allowed.has(key)) || keys.length === 0) return { ok: false };
  if (mode === "create" && !["evidence_id_a", "evidence_id_b", "relation_type", "rationale", "provenance_note"].every((key) => Object.hasOwn(input, key))) {
    return { ok: false };
  }
  if (mode === "update" && keys.some((key) => key === "evidence_id_a" || key === "evidence_id_b")) {
    return { ok: false };
  }

  const value: RelationshipMutation = {};
  for (const key of ["evidence_id_a", "evidence_id_b"] as const) {
    if (Object.hasOwn(input, key)) {
      if (typeof input[key] !== "string" || !UUID_PATTERN.test(input[key])) return { ok: false };
      value[key] = input[key];
    }
  }
  if (Object.hasOwn(input, "relation_type")) {
    if (!["supports", "contradicts", "contextualizes", "derived_from"].includes(String(input.relation_type))) {
      return { ok: false };
    }
    value.relation_type = input.relation_type as WorkspaceEvidenceRelationship["relation_type"];
  }
  for (const key of ["rationale", "provenance_note"] as const) {
    if (Object.hasOwn(input, key)) {
      const max = key === "rationale" ? 4000 : 2000;
      if (typeof input[key] !== "string" || !input[key].trim() || input[key].length > max) return { ok: false };
      value[key] = input[key].trim();
    }
  }
  if (mode === "create" && value.evidence_id_a === value.evidence_id_b) return { ok: false };
  if (mode === "update" && !["relation_type", "rationale", "provenance_note"].some((key) => Object.hasOwn(value, key))) {
    return { ok: false };
  }
  return { ok: true, value };
}

function safeRelationship(
  value: unknown,
  workspaceId: string,
): WorkspaceEvidenceRelationship | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const uuidFields = ["id", "evidence_id_a", "evidence_id_b", "created_by", "updated_by"] as const;
  if (
    uuidFields.some((field) => typeof candidate[field] !== "string" || !UUID_PATTERN.test(candidate[field] as string)) ||
    candidate.workspace_id !== workspaceId ||
    !["supports", "contradicts", "contextualizes", "derived_from"].includes(String(candidate.relation_type)) ||
    typeof candidate.rationale !== "string" ||
    candidate.rationale.length > 4000 ||
    typeof candidate.provenance_note !== "string" ||
    candidate.provenance_note.length > 2000 ||
    typeof candidate.created_at !== "string" ||
    !Number.isFinite(Date.parse(candidate.created_at)) ||
    typeof candidate.updated_at !== "string" ||
    !Number.isFinite(Date.parse(candidate.updated_at)) ||
    String(candidate.evidence_id_a) >= String(candidate.evidence_id_b)
  ) return null;
  return candidate as WorkspaceEvidenceRelationship;
}

export async function proxyWorkspaceRelationshipsRequest({
  request,
  workspaceId,
  relationshipId,
  method,
  body,
}: {
  request: Request;
  workspaceId: string;
  relationshipId?: string;
  method: "GET" | "POST" | "PATCH" | "DELETE";
  body?: RelationshipMutation;
}): Promise<Response> {
  if (!isUuid(workspaceId) || (relationshipId !== undefined && !isUuid(relationshipId))) {
    return json(404, { error: "Relationship not found." });
  }
  if (method !== "GET" && !sameOrigin(request)) {
    return json(403, { error: "Request origin was not accepted." });
  }
  const auth = await getVerifiedAuthContext();
  if (auth.kind === "unauthenticated") return json(401, { error: "Authentication is required." });
  if (auth.kind === "unavailable") return json(503, { error: "Identity verification is unavailable." });
  const blocked = await mfaBlocked(auth);
  if (blocked) return blocked;
  const serviceUrl = getEvidenceServiceBaseUrl();
  if (!serviceUrl) return json(503, { error: "Evidence service is unavailable." });

  const path = `/v1/workspaces/${workspaceId}/relationships${relationshipId ? `/${relationshipId}` : ""}`;
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
  if (upstream.status === 404) return json(404, { error: "Relationship or workspace not found." });
  if (upstream.status === 409) return json(409, { error: "That relationship already exists or conflicts with current evidence." });
  if (upstream.status === 422) return json(400, { error: "Relationship input was not accepted." });
  if (upstream.status >= 500 || upstream.status >= 300) return json(503, { error: "Evidence service is unavailable." });

  const expectedStatus = method === "POST" ? 201 : method === "DELETE" ? 204 : 200;
  if (upstream.status !== expectedStatus) return json(502, { error: "Evidence service returned an invalid response." });
  if (method === "DELETE") return new Response(null, { status: 204, headers: { "Cache-Control": "no-store" } });

  let payload: unknown;
  try {
    payload = await upstream.json();
  } catch {
    return json(502, { error: "Evidence service returned an invalid response." });
  }
  if (!relationshipId && method === "GET") {
    if (!Array.isArray(payload)) return json(502, { error: "Evidence service returned an invalid response." });
    const relationships = payload.map((item) => safeRelationship(item, workspaceId));
    return relationships.some((item) => item === null)
      ? json(502, { error: "Evidence service returned an invalid response." })
      : json(200, relationships);
  }
  const relationship = safeRelationship(payload, workspaceId);
  return relationship && (relationshipId === undefined || relationship.id === relationshipId)
    ? json(method === "POST" ? 201 : 200, relationship)
    : json(502, { error: "Evidence service returned an invalid response." });
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
  const blocked = await mfaBlocked(auth);
  if (blocked) return blocked;

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
  const blocked = await mfaBlocked(auth);
  if (blocked) return blocked;

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
