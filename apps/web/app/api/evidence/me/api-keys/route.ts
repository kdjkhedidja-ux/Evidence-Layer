import { proxyPersonalApiKeyRequest } from "../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return proxyPersonalApiKeyRequest({ request, method: "GET" });
}

export async function POST(request: Request): Promise<Response> {
  return proxyPersonalApiKeyRequest({ request, method: "POST" });
}
