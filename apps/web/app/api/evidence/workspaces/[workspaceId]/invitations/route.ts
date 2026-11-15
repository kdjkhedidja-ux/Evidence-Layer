import { proxyWorkspaceInvitationsRequest } from "../../../../../../lib/evidence-api";

export async function GET(
  request: Request,
  context: { params: Promise<{ workspaceId: string }> },
): Promise<Response> {
  const { workspaceId } = await context.params;
  return proxyWorkspaceInvitationsRequest({ request, workspaceId });
}

export async function POST(
  request: Request,
  context: { params: Promise<{ workspaceId: string }> },
): Promise<Response> {
  const { workspaceId } = await context.params;
  return proxyWorkspaceInvitationsRequest({ request, workspaceId });
}
