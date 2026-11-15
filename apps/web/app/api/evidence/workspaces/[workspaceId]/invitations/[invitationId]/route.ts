import { proxyWorkspaceInvitationRevokeRequest } from "../../../../../../../lib/evidence-api";

export async function DELETE(
  request: Request,
  context: { params: Promise<{ workspaceId: string; invitationId: string }> },
): Promise<Response> {
  const { workspaceId, invitationId } = await context.params;
  return proxyWorkspaceInvitationRevokeRequest({ request, workspaceId, invitationId });
}
