import {
  proxyStageWorkspaceSourceRequest,
  proxyWorkspaceSourcesRequest,
} from "../../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ workspaceId: string }> },
): Promise<Response> {
  const { workspaceId } = await context.params;
  return proxyWorkspaceSourcesRequest({ workspaceId });
}

export async function POST(
  request: Request,
  context: { params: Promise<{ workspaceId: string }> },
): Promise<Response> {
  const { workspaceId } = await context.params;
  return proxyStageWorkspaceSourceRequest({ request, workspaceId });
}
