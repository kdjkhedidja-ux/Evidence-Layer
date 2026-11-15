import { proxyWorkspaceSourceGrantsRequest } from "../../../../../../../../lib/evidence-api";

export async function GET(
  request: Request,
  context: { params: Promise<{ workspaceId: string; sourceId: string }> },
): Promise<Response> {
  const { workspaceId, sourceId } = await context.params;
  return proxyWorkspaceSourceGrantsRequest({ request, workspaceId, sourceId });
}

export async function POST(
  request: Request,
  context: { params: Promise<{ workspaceId: string; sourceId: string }> },
): Promise<Response> {
  const { workspaceId, sourceId } = await context.params;
  return proxyWorkspaceSourceGrantsRequest({ request, workspaceId, sourceId });
}
