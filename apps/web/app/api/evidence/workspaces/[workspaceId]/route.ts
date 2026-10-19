import { isUuid, proxyEvidenceRequest } from "../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  context: { params: Promise<{ workspaceId: string }> },
): Promise<Response> {
  const { workspaceId } = await context.params;
  if (!isUuid(workspaceId)) {
    return Response.json(
      { error: "Workspace not found." },
      { status: 404, headers: { "Cache-Control": "no-store" } },
    );
  }
  return proxyEvidenceRequest({
    endpoint: `/v1/workspaces/${workspaceId}`,
    method: "GET",
    request,
    responseKind: "workspace",
  });
}
