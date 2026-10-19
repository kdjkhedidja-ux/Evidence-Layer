import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AuthAccount from "../components/auth-account";

const mocks = vi.hoisted(() => {
  const replace = vi.fn();
  const refresh = vi.fn();
  return {
    replace,
    refresh,
    router: { replace, refresh },
    signOut: vi.fn(),
    onAuthStateChange: vi.fn(),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => mocks.router,
}));
vi.mock("../lib/supabase/client", () => ({
  createSupabaseBrowserClient: () => ({ auth: {
    signOut: mocks.signOut,
    onAuthStateChange: mocks.onAuthStateChange,
  } }),
}));

describe("authenticated workspace controls", () => {
  beforeEach(() => {
    mocks.signOut.mockResolvedValue({ error: null });
    mocks.onAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
  });

  it("reports an expired service session without exposing response details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "secret detail" }), { status: 401 })));
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your session expired");
    expect(screen.queryByText("secret detail")).not.toBeInTheDocument();
  });

  it("automatically provisions an empty account and verifies its personal workspace", async () => {
    const workspace = {
      id: "10000000-0000-4000-8000-000000000001",
      name: "Personal workspace",
      role: "owner",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(workspace))
      .mockResolvedValueOnce(Response.json({ user_id: "user-a", workspaces: [workspace] }))
      .mockResolvedValueOnce(Response.json(workspace));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    await screen.findByRole("button", { name: "Verify access" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/evidence/personal-workspace");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "PUT" });
    expect(fetchMock.mock.calls[0][1]).not.toHaveProperty("body");
    await user.click(await screen.findByRole("button", { name: "Verify access" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Access verified");
    expect(fetchMock.mock.calls[2][0]).toContain("/api/evidence/workspaces/");
  });

  it("does not claim the workspace is ready when the membership reload fails", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(Response.json({ id: "10000000-0000-4000-8000-000000000001", name: "Personal", role: "owner" }))
      .mockResolvedValueOnce(new Response(null, { status: 503 })));
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Workspace service is unavailable"));
    expect(screen.queryByText("Your personal workspace is ready.")).not.toBeInTheDocument();
  });

  it("does not claim the provisioned workspace is ready when its membership is absent", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(Response.json({ id: "10000000-0000-4000-8000-000000000001", name: "Personal", role: "owner" }))
      .mockResolvedValueOnce(Response.json({ user_id: "user-a", workspaces: [] })));
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    expect(await screen.findByText("Workspace access could not be confirmed. Try again later.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry workspace setup" })).toBeInTheDocument();
    expect(screen.queryByText("Your personal workspace is ready.")).not.toBeInTheDocument();
  });

  it("retries workspace setup without changing the signed-in account", async () => {
    const workspace = { id: "10000000-0000-4000-8000-000000000001", name: "Personal", role: "owner" };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(Response.json(workspace))
      .mockResolvedValueOnce(Response.json({ user_id: "user-a", workspaces: [workspace] }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<AuthAccount userId="user-a" email="a@example.test" />);

    await user.click(await screen.findByRole("button", { name: "Retry workspace setup" }));
    expect(await screen.findByRole("button", { name: "Verify access" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(mocks.signOut).not.toHaveBeenCalled();
  });

  it("does not provision a second personal workspace when memberships already exist", async () => {
    const workspace = {
      id: "10000000-0000-4000-8000-000000000001",
      name: "Personal workspace",
      role: "owner",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(workspace))
      .mockResolvedValueOnce(Response.json({ user_id: "user-a", workspaces: [workspace] }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthAccount userId="user-a" email="a@example.test" />);

    await screen.findByRole("button", { name: "Verify access" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/evidence/personal-workspace", { method: "PUT", cache: "no-store" });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/evidence/me", { cache: "no-store" });
  });

  it("signs out through Supabase Auth", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ user_id: "user-a", workspaces: [] })));
    const user = userEvent.setup();
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(mocks.signOut).toHaveBeenCalledOnce());
    expect(mocks.replace).toHaveBeenCalledWith("/login?reason=signed-out");
  });

  it("does not render workspace data after an account switch", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ id: "10000000-0000-4000-8000-000000000001", name: "Personal", role: "owner" }))
      .mockResolvedValueOnce(Response.json({ user_id: "user-b", workspaces: [
        { id: "20000000-0000-4000-8000-000000000002", name: "Other private workspace", role: "owner" },
      ] }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("session expired");
    expect(screen.queryByText("Other private workspace")).not.toBeInTheDocument();
    expect(mocks.refresh).toHaveBeenCalled();
  });
});
