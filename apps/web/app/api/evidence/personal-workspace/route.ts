import { proxyEvidenceRequest } from "../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function PUT(request: Request): Promise<Response> {
  return proxyEvidenceRequest({
    endpoint: "/v1/me/personal-workspace",
    method: "PUT",
    request,
    requireSameOrigin: true,
    responseKind: "workspace",
  });
}
