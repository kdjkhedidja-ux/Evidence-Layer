import {
  parseEvidenceMutation,
  proxyWorkspaceEvidenceRequest,
} from "../../../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ workspaceId: string; evidenceId: string }> },
): Promise<Response> {
  const { workspaceId, evidenceId } = await context.params;
  const parsed = await parseEvidenceMutation(request, "update");
  if (!parsed.ok) {
    return Response.json(
      { error: "Evidence input was not accepted." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  return proxyWorkspaceEvidenceRequest({
    request,
    workspaceId,
    evidenceId,
    method: "PATCH",
    body: parsed.value,
  });
}

export async function DELETE(
  request: Request,
  context: { params: Promise<{ workspaceId: string; evidenceId: string }> },
): Promise<Response> {
  const { workspaceId, evidenceId } = await context.params;
  return proxyWorkspaceEvidenceRequest({
    request,
    workspaceId,
    evidenceId,
    method: "DELETE",
  });
}
