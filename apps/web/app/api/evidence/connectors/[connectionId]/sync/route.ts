import { proxySyncConnector } from "../../../../../../lib/connector-api";

export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  context: { params: Promise<{ connectionId: string }> },
): Promise<Response> {
  const { connectionId } = await context.params;
  return proxySyncConnector(connectionId, request);
}
