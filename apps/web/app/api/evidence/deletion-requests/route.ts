import { proxyDeletionRequest } from "../../../../lib/deletion-api";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return Response.json(
      { error: "A JSON body is required." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  const candidate = payload as { idempotency_key?: unknown } | null;
  const key = candidate && typeof candidate.idempotency_key === "string"
    ? candidate.idempotency_key.trim()
    : "";
  if (key.length < 1 || key.length > 256) {
    return Response.json(
      { error: "idempotency_key must be between 1 and 256 characters." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  return proxyDeletionRequest({
    endpoint: "/v1/deletion-requests",
    method: "POST",
    request,
    requireSameOrigin: true,
    body: { idempotency_key: key },
  });
}
