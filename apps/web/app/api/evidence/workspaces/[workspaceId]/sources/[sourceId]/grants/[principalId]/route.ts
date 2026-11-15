import { proxyWorkspaceSourceGrantDeleteRequest } from "../../../../../../../../../lib/evidence-api";

export async function DELETE(
  request: Request,
  context: { params: Promise<{ workspaceId: string; sourceId: string; principalId: string }> },
): Promise<Response> {
  const { workspaceId, sourceId, principalId } = await context.params;
  return proxyWorkspaceSourceGrantDeleteRequest({ request, workspaceId, sourceId, principalId });
}
