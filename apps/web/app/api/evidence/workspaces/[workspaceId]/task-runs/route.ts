import { proxyWorkspaceTaskRunsRequest } from "../../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  context: { params: Promise<{ workspaceId: string }> },
): Promise<Response> {
  const { workspaceId } = await context.params;
  return proxyWorkspaceTaskRunsRequest({ request, workspaceId });
}
