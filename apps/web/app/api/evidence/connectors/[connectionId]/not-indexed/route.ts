import { proxyListNotIndexed } from "../../../../../../lib/connector-api";

export const dynamic = "force-dynamic";

// Files of this source that are recorded but not indexed (too large, binary,
// fetch error, empty). Owner only: the service reads them with the caller's
// session under RLS.
export async function GET(
  _request: Request,
  context: { params: Promise<{ connectionId: string }> },
): Promise<Response> {
  const { connectionId } = await context.params;
  return proxyListNotIndexed(connectionId);
}
