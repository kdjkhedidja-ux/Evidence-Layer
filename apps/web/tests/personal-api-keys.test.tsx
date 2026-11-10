import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PersonalApiKeys from "../app/workspace/personal-api-keys";

const WORKSPACE = {
  id: "22222222-2222-4222-8222-222222222222",
  name: "API workspace",
  role: "owner" as const,
};

const KEY = {
  id: "66666666-6666-4666-8666-666666666666",
  workspace_id: WORKSPACE.id,
  name: "Reporting export",
  key_prefix: "evl_pk_66666666",
  scopes: ["evidence:read", "relationships:read"] as const,
  created_at: "2026-11-10T10:00:00Z",
  expires_at: "2026-12-10T10:00:00Z",
  revoked_at: null,
  last_used_at: null,
};

describe("Personal API keys panel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("lists existing keys and creates a one-time token", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api-keys") && init?.method === "POST") {
        return Response.json({ ...KEY, token: `evl_pk_${KEY.id}.${"A".repeat(43)}` }, { status: 201 });
      }
      if (url.endsWith("/api-keys")) return Response.json([KEY]);
      return Response.json({}, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<PersonalApiKeys workspaces={[WORKSPACE]} />);
    expect(await screen.findByText("Reporting export")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Key name"), { target: { value: "Export key" } });
    fireEvent.click(screen.getByRole("button", { name: "Create API key" }));

    expect(await screen.findByText(/will not be shown again/i)).toBeInTheDocument();
    expect(screen.getByText(`evl_pk_${KEY.id}.${"A".repeat(43)}`)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/evidence/me/api-keys",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          workspace_id: WORKSPACE.id,
          name: "Export key",
          expiration_days: 90,
        }),
      }),
    );
  });

  it("requires a second click before revoking", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api-keys/") && init?.method === "DELETE") {
        return Response.json({ id: KEY.id, revoked: true });
      }
      if (url.endsWith("/api-keys")) return Response.json([KEY]);
      return Response.json({}, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<PersonalApiKeys workspaces={[WORKSPACE]} />);
    expect(await screen.findByText("Reporting export")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(screen.getByRole("button", { name: "Confirm revoke" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining(`/api-keys/${KEY.id}`),
      expect.objectContaining({ method: "DELETE" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/evidence/me/api-keys/${KEY.id}`,
        expect.objectContaining({ method: "DELETE" }),
      );
    });
    expect(await screen.findByText(/API key revoked/)).toBeInTheDocument();
  });
});
