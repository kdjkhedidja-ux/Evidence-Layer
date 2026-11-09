import { proxyDeletionRequest } from "../../../../../lib/deletion-api";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return proxyDeletionRequest({
    endpoint: "/v1/me/deletion-request",
    method: "GET",
    request,
  });
}
