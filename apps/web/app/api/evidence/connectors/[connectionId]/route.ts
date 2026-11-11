import { proxyDeleteConnector } from "../../../../../lib/connector-api";

export const dynamic = "force-dynamic";

export async function DELETE(
  request: Request,
  context: { params: Promise<{ connectionId: string }> },
): Promise<Response> {
  const { connectionId } = await context.params;
  return proxyDeleteConnector(connectionId, request);
}
