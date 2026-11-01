import {
  parseEvidenceMutation,
  proxyWorkspaceEvidenceRequest,
} from "../../../../../../lib/evidence-api";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  context: { params: Promise<{ workspaceId: string }> },
): Promise<Response> {
  const { workspaceId } = await context.params;
  return proxyWorkspaceEvidenceRequest({
    request,
    workspaceId,
    method: "GET",
  });
}

export async function POST(
  request: Request,
  context: { params: Promise<{ workspaceId: string }> },
): Promise<Response> {
  const { workspaceId } = await context.params;
  const parsed = await parseEvidenceMutation(request, "create");
  if (!parsed.ok) {
    return Response.json(
      { error: "Evidence input was not accepted." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  const response = await proxyWorkspaceEvidenceRequest({
    request,
    workspaceId,
    method: "POST",
    body: parsed.value,
  });
  if (response.status !== 200) return response;
  return Response.json(await response.json(), {
    status: 201,
    headers: { "Cache-Control": "no-store", "Content-Type": "application/json" },
  });
}
