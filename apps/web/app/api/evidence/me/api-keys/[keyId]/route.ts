import { proxyPersonalApiKeyRequest } from "../../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function DELETE(
  request: Request,
  context: { params: Promise<{ keyId: string }> },
): Promise<Response> {
  const { keyId } = await context.params;
  return proxyPersonalApiKeyRequest({ request, method: "DELETE", keyId });
}
