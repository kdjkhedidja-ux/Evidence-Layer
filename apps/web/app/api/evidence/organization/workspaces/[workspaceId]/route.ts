import { isUuid, proxyOrganizationRequest } from "../../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function DELETE(
  request: Request,
  context: { params: Promise<{ workspaceId: string }> },
): Promise<Response> {
  const { workspaceId } = await context.params;
  if (!isUuid(workspaceId)) {
    return Response.json({ error: "Invalid workspace identifier." }, { status: 400 });
  }
  return proxyOrganizationRequest({
    endpoint: `/v1/organization/workspaces/${workspaceId}`,
    method: "DELETE",
    request,
    responseKind: "workspaceDelete",
  });
}
