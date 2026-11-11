import { proxyRevokeAccountExport } from "../../../../../../../lib/export-api";

export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  context: { params: Promise<{ exportId: string }> },
): Promise<Response> {
  const { exportId } = await context.params;
  return proxyRevokeAccountExport(exportId, request);
}
