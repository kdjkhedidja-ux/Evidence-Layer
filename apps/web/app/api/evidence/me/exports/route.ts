import { proxyCreateAccountExport } from "../../../../../lib/export-api";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  return proxyCreateAccountExport(request);
}
