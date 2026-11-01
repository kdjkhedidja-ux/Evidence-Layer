import {
  parseRelationshipMutation,
  proxyWorkspaceRelationshipsRequest,
} from "../../../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ workspaceId: string; relationshipId: string }> },
): Promise<Response> {
  const { workspaceId, relationshipId } = await context.params;
  const parsed = await parseRelationshipMutation(request, "update");
  if (!parsed.ok) {
    return Response.json(
      { error: "Relationship input was not accepted." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  return proxyWorkspaceRelationshipsRequest({
    request,
    workspaceId,
    relationshipId,
    method: "PATCH",
    body: parsed.value,
  });
}

export async function DELETE(
  request: Request,
  context: { params: Promise<{ workspaceId: string; relationshipId: string }> },
): Promise<Response> {
  const { workspaceId, relationshipId } = await context.params;
  return proxyWorkspaceRelationshipsRequest({
    request,
    workspaceId,
    relationshipId,
    method: "DELETE",
  });
}
