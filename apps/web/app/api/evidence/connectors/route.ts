import { proxyListConnectors } from "../../../../lib/connector-api";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return proxyListConnectors(request);
}
