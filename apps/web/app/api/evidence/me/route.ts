import { proxyEvidenceRequest } from "../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return proxyEvidenceRequest({
    endpoint: "/v1/me",
    method: "GET",
    request,
    responseKind: "me",
  });
}
