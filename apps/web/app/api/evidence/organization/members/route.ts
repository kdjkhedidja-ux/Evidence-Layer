import { proxyOrganizationRequest } from "../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "A member email and role are required." }, { status: 400 });
  }
  const candidate = body && typeof body === "object" ? (body as Record<string, unknown>) : {};
  const email = candidate.email;
  const role = candidate.role;
  if (
    typeof email !== "string" ||
    !email.includes("@") ||
    (role !== "admin" && role !== "member")
  ) {
    return Response.json({ error: "A member email and role are required." }, { status: 400 });
  }
  return proxyOrganizationRequest({
    endpoint: "/v1/organization/members",
    method: "POST",
    request,
    body: { email: email.trim(), role },
    responseKind: "member",
  });
}
