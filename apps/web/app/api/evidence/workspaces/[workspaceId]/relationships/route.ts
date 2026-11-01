import {
  parseRelationshipMutation,
  proxyWorkspaceRelationshipsRequest,
} from "../../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  context: { params: Promise<{ workspaceId: string }> },
): Promise<Response> {
  const { workspaceId } = await context.params;
  return proxyWorkspaceRelationshipsRequest({ request, workspaceId, method: "GET" });
}

export async function POST(
  request: Request,
  context: { params: Promise<{ workspaceId: string }> },
): Promise<Response> {
  const { workspaceId } = await context.params;
  const parsed = await parseRelationshipMutation(request, "create");
  if (!parsed.ok) {
    return Response.json(
      { error: "Relationship input was not accepted." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  return proxyWorkspaceRelationshipsRequest({
    request,
    workspaceId,
    method: "POST",
    body: parsed.value,
  });
}
