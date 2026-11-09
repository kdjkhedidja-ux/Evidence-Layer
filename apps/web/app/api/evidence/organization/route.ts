import { proxyOrganizationRequest } from "../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return proxyOrganizationRequest({
    endpoint: "/v1/organization",
    method: "GET",
    request,
    responseKind: "overview",
  });
}
