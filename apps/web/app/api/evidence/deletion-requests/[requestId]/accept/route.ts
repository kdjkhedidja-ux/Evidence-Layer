import { isUuid } from "../../../../../../lib/evidence-api";
import { proxyDeletionRequest } from "../../../../../../lib/deletion-api";

export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  context: { params: Promise<{ requestId: string }> },
): Promise<Response> {
  const { requestId } = await context.params;
  if (!isUuid(requestId)) {
    return Response.json(
      { error: "Deletion request not found." },
      { status: 404, headers: { "Cache-Control": "no-store" } },
    );
  }
  return proxyDeletionRequest({
    endpoint: `/v1/deletion-requests/${requestId}/accept`,
    method: "POST",
    request,
    requireSameOrigin: true,
  });
}
