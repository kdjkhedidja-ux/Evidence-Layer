export const ACTIVE_WORKSPACE_STORAGE_KEY = "evidence-layer.active-workspace-id";

export function readActiveWorkspaceId(): string | null {
  try {
    return globalThis.localStorage?.getItem(ACTIVE_WORKSPACE_STORAGE_KEY) ?? null;
  } catch {
    return null;
  }
}

export function writeActiveWorkspaceId(workspaceId: string): void {
  try {
    if (workspaceId) {
      globalThis.localStorage?.setItem(ACTIVE_WORKSPACE_STORAGE_KEY, workspaceId);
    } else {
      globalThis.localStorage?.removeItem(ACTIVE_WORKSPACE_STORAGE_KEY);
    }
  } catch {
    // Storage can be unavailable in restricted browser contexts.
  }
}
