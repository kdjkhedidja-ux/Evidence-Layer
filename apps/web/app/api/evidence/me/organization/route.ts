import { proxyCreateOrganization } from "../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  return proxyCreateOrganization(request);
}
