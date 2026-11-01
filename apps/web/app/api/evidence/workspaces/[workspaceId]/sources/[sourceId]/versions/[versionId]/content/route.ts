import { proxyVerifiedSourceContent } from "../../../../../../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: {
    params: Promise<{ workspaceId: string; sourceId: string; versionId: string }>;
  },
): Promise<Response> {
  const { workspaceId, sourceId, versionId } = await context.params;
  return proxyVerifiedSourceContent({
    workspaceId,
    sourceId,
    versionId,
  });
}
