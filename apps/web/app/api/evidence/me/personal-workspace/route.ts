import { proxyPersonalDeletion } from "../../../../../lib/deletion-api";

export const dynamic = "force-dynamic";

export async function DELETE(request: Request): Promise<Response> {
  return proxyPersonalDeletion({
    endpoint: "/v1/me/personal-workspace",
    method: "DELETE",
    request,
  });
}
