import { proxyWorkspaceDeletionAction } from "../../../../../../../../lib/evidence-api";

export async function POST(
  request: Request,
  context: { params: Promise<{ workspaceId: string; requestId: string }> },
): Promise<Response> {
  const { workspaceId, requestId } = await context.params;
  return proxyWorkspaceDeletionAction({
    request,
    workspaceId,
    requestId,
    action: "withdraw",
  });
}
