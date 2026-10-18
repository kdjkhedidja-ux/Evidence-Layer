import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AuthAccount from "../components/auth-account";

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  refresh: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace, refresh: mocks.refresh }),
}));
vi.mock("../lib/supabase/client", () => ({
  createSupabaseBrowserClient: () => ({ auth: { signOut: mocks.signOut } }),
}));

describe("authenticated workspace controls", () => {
  beforeEach(() => {
    mocks.signOut.mockResolvedValue({ error: null });
  });

  it("reports an expired service session without exposing response details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "secret detail" }), { status: 401 })));
    render(<AuthAccount />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your session expired");
    expect(screen.queryByText("secret detail")).not.toBeInTheDocument();
  });

  it("provisions only the signed-in user's personal workspace and verifies access", async () => {
    const workspace = {
      id: "10000000-0000-4000-8000-000000000001",
      name: "Personal workspace",
      role: "owner",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ workspaces: [] }))
      .mockResolvedValueOnce(Response.json(workspace))
      .mockResolvedValueOnce(Response.json({ workspaces: [workspace] }))
      .mockResolvedValueOnce(Response.json(workspace));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<AuthAccount />);
    await user.click(await screen.findByRole("button", { name: "Create personal workspace" }));
    expect(fetchMock.mock.calls[1][0]).toBe("/api/evidence/personal-workspace");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "PUT" });
    expect(fetchMock.mock.calls[1][1]).not.toHaveProperty("body");
    await user.click(await screen.findByRole("button", { name: "Verify access" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Access verified");
    expect(fetchMock.mock.calls[3][0]).toContain("/api/evidence/workspaces/");
  });

  it("signs out through Supabase Auth", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ workspaces: [] })));
    const user = userEvent.setup();
    render(<AuthAccount />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(mocks.signOut).toHaveBeenCalledOnce());
    expect(mocks.replace).toHaveBeenCalledWith("/login?reason=signed-out");
  });
});
