import { proxyAcceptWorkspaceInvitationRequest } from "../../../../../lib/evidence-api";

export async function POST(request: Request): Promise<Response> {
  return proxyAcceptWorkspaceInvitationRequest({ request });
}
