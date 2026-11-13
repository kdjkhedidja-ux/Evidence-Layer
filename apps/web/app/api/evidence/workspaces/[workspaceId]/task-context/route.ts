import {
  parseWorkspaceTaskContextInput,
  proxyWorkspaceTaskContextRequest,
} from "../../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  context: { params: Promise<{ workspaceId: string }> },
): Promise<Response> {
  const { workspaceId } = await context.params;
  const parsed = await parseWorkspaceTaskContextInput(request);
  if (!parsed.ok) {
    return Response.json(
      { error: "Task context input was not accepted." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  return proxyWorkspaceTaskContextRequest({
    request,
    workspaceId,
    body: parsed.value,
  });
}
