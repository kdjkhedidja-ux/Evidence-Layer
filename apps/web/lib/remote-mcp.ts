/** Remote MCP is independent of the provider connector flags. */
export function remoteMcpEnabled({ flag }: { flag?: string }): boolean {
  return flag === "true";
}

export function remoteMcpWorkspaceUrl({
  baseUrl,
  workspaceId,
}: {
  baseUrl?: string;
  workspaceId?: string;
}): string | null {
  const base = baseUrl?.trim().replace(/\/+$/, "");
  const workspace = workspaceId?.trim();
  if (!base || !workspace) return null;
  return `${base}/workspaces/${encodeURIComponent(workspace)}/`;
}
