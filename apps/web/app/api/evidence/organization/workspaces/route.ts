import { proxyOrganizationRequest } from "../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "A workspace name is required." }, { status: 400 });
  }
  const name = body && typeof body === "object" ? (body as Record<string, unknown>).name : null;
  if (typeof name !== "string" || name.trim().length < 1 || name.trim().length > 120) {
    return Response.json({ error: "Workspace name must be 1-120 characters." }, { status: 400 });
  }
  return proxyOrganizationRequest({
    endpoint: "/v1/organization/workspaces",
    method: "POST",
    request,
    body: { name: name.trim() },
    responseKind: "workspace",
  });
}
