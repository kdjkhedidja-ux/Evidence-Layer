import { getEvidenceServiceBaseUrl } from "./supabase/config";
import { getVerifiedAuthContext } from "./supabase/server";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const STATUSES = [
  "approval_pending",
  "journal_ack_pending",
  "accepted",
  "purge_pending",
  "purge_failed",
  "purged",
] as const;

export type DeletionStatus = (typeof STATUSES)[number];

export type DeletionRequestView = {
  request_id: string;
  requester_user_id: string;
  scope_type: "account";
  status: DeletionStatus;
  created_at: string;
  accepted_at: string | null;
};

function json(status: number, payload: unknown): Response {
  return Response.json(payload, {
    status,
    headers: { "Cache-Control": "no-store", "Content-Type": "application/json" },
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

export function safeDeletionRequest(value: unknown, userId: string): DeletionRequestView | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.request_id !== "string" ||
    !UUID_PATTERN.test(candidate.request_id) ||
    candidate.requester_user_id !== userId ||
    candidate.scope_type !== "account" ||
    typeof candidate.status !== "string" ||
    !(STATUSES as readonly string[]).includes(candidate.status) ||
    typeof candidate.created_at !== "string" ||
    (candidate.accepted_at !== null && typeof candidate.accepted_at !== "string")
  ) {
    return null;
  }
  return {
    request_id: candidate.request_id,
    requester_user_id: candidate.requester_user_id as string,
    scope_type: "account",
    status: candidate.status as DeletionStatus,
    created_at: candidate.created_at,
    accepted_at: candidate.accepted_at as string | null,
  };
}

type DeletionProxyOptions = {
  endpoint: string;
  method: "GET" | "POST";
  request: Request;
  requireSameOrigin?: boolean;
  body?: unknown;
};

export async function proxyDeletionRequest({
  endpoint,
  method,
  request,
  requireSameOrigin = false,
  body,
}: DeletionProxyOptions): Promise<Response> {
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
  if (upstream.status === 403) return json(403, { error: "This action is not available for this account." });
  if (upstream.status === 404) return json(404, { error: "Deletion request not found." });
  if (upstream.status === 409) {
    return json(409, { error: "A deletion request is already open for this account." });
  }
  if (upstream.status === 400 || upstream.status === 422) {
    return json(400, { error: "The deletion request was not accepted." });
  }
  if (upstream.status !== 200 && upstream.status !== 201) {
    return json(503, { error: "Evidence service is unavailable." });
  }

  let payload: unknown;
  try {
    payload = await upstream.json();
  } catch {
    return json(502, { error: "Evidence service returned an invalid response." });
  }

  const parsed = safeDeletionRequest(payload, auth.user.id);
  if (!parsed) return json(502, { error: "Evidence service returned an invalid response." });
  return json(upstream.status, parsed);
}

export type PersonalDeletionView = {
  workspace_id: string;
  evidence_deleted: number;
  relationships_deleted: number;
  source_grants_deleted: number;
  snapshots_deleted: number;
  storage_cleanup_queued: number;
  storage_cleanup_required: boolean;
  workspace_deleted: boolean;
};

function nonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

export function safePersonalDeletion(value: unknown): PersonalDeletionView | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.workspace_id !== "string" ||
    !UUID_PATTERN.test(candidate.workspace_id) ||
    !nonNegativeInteger(candidate.evidence_deleted) ||
    !nonNegativeInteger(candidate.relationships_deleted) ||
    !nonNegativeInteger(candidate.source_grants_deleted) ||
    !nonNegativeInteger(candidate.snapshots_deleted) ||
    !nonNegativeInteger(candidate.storage_cleanup_queued) ||
    typeof candidate.storage_cleanup_required !== "boolean" ||
    typeof candidate.workspace_deleted !== "boolean"
  ) {
    return null;
  }
  return {
    workspace_id: candidate.workspace_id,
    evidence_deleted: candidate.evidence_deleted,
    relationships_deleted: candidate.relationships_deleted,
    source_grants_deleted: candidate.source_grants_deleted,
    snapshots_deleted: candidate.snapshots_deleted,
    storage_cleanup_queued: candidate.storage_cleanup_queued,
    storage_cleanup_required: candidate.storage_cleanup_required,
    workspace_deleted: candidate.workspace_deleted,
  };
}

type PersonalDeletionProxyOptions = {
  endpoint: string;
  method: "POST" | "DELETE";
  request: Request;
};

export async function proxyPersonalDeletion({
  endpoint,
  method,
  request,
}: PersonalDeletionProxyOptions): Promise<Response> {
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
  if (upstream.status !== 200 && upstream.status !== 202) {
    return json(503, { error: "Evidence service is unavailable." });
  }

  let payload: unknown;
  try {
    payload = await upstream.json();
  } catch {
    return json(502, { error: "Evidence service returned an invalid response." });
  }

  const parsed = safePersonalDeletion(payload);
  if (!parsed) return json(502, { error: "Evidence service returned an invalid response." });
  return json(upstream.status, parsed);
}
