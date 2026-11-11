import { proxyDownloadAccountExport } from "../../../../../../../lib/export-api";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  context: { params: Promise<{ exportId: string }> },
): Promise<Response> {
  const { exportId } = await context.params;
  return proxyDownloadAccountExport(exportId, request);
}
