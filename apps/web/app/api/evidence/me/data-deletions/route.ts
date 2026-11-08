import { proxyPersonalDeletion } from "../../../../../lib/deletion-api";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  return proxyPersonalDeletion({
    endpoint: "/v1/me/data-deletions",
    method: "POST",
    request,
  });
}
