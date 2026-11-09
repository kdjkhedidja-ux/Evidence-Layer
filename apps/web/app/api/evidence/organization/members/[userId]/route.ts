import { isUuid, proxyOrganizationRequest } from "../../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ userId: string }> },
): Promise<Response> {
  const { userId } = await context.params;
  if (!isUuid(userId)) {
    return Response.json({ error: "Invalid member identifier." }, { status: 400 });
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "A role is required." }, { status: 400 });
  }
  const role = body && typeof body === "object" ? (body as Record<string, unknown>).role : null;
  if (role !== "admin" && role !== "member") {
    return Response.json({ error: "A role is required." }, { status: 400 });
  }
  return proxyOrganizationRequest({
    endpoint: `/v1/organization/members/${userId}`,
    method: "PATCH",
    request,
    body: { role },
    responseKind: "memberRole",
  });
}

export async function DELETE(
  request: Request,
  context: { params: Promise<{ userId: string }> },
): Promise<Response> {
  const { userId } = await context.params;
  if (!isUuid(userId)) {
    return Response.json({ error: "Invalid member identifier." }, { status: 400 });
  }
  return proxyOrganizationRequest({
    endpoint: `/v1/organization/members/${userId}`,
    method: "DELETE",
    request,
    responseKind: "memberRemoval",
  });
}
