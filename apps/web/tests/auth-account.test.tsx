import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
    window.localStorage.clear();
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

describe("account deletion request", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_DELETION_REQUESTS_ENABLED", "true");
    mocks.signOut.mockResolvedValue({ error: null });
    mocks.onAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  const workspace = { id: "10000000-0000-4000-8000-000000000002", name: "Personal", role: "owner" };
  const deletion = {
    request_id: "72000000-0000-4000-8000-000000000001",
    requester_user_id: "user-a",
    scope_type: "account",
    status: "approval_pending",
    created_at: "2026-11-06T12:00:00Z",
    accepted_at: null,
  };

  function mockDeletionFlow(overrides: { create?: Response; accept?: Response } = {}) {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/evidence/me")) {
        return Response.json({ user_id: "user-a", workspaces: [workspace] });
      }
      if (url.endsWith("/api/evidence/deletion-requests")) {
        return overrides.create ?? Response.json(deletion, { status: 201 });
      }
      if (url.endsWith("/accept")) {
        return overrides.accept ?? Response.json({ ...deletion, status: "journal_ack_pending", accepted_at: "2026-11-06T12:01:00Z" });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("stays hidden unless the deployment explicitly enables it", async () => {
    vi.stubEnv("NEXT_PUBLIC_DELETION_REQUESTS_ENABLED", "false");
    mockDeletionFlow();
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    await waitFor(() => expect(screen.queryByText("Checking workspace access…")).not.toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "Delete account" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Request account deletion" })).not.toBeInTheDocument();
  });

  it("states the real deletion scope without promising full erasure", async () => {
    mockDeletionFlow();
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    expect(await screen.findByRole("heading", { name: "Delete account" })).toBeInTheDocument();
    expect(screen.getByText(/personal workspace and its data, snapshots, stored files/i)).toBeInTheDocument();
    expect(screen.getByText(/Shared workspace content owned by other members is not deleted/i)).toBeInTheDocument();
    expect(screen.getByText(/cannot be cancelled/i)).toBeInTheDocument();
  });

  it("creates a request, requires confirmation, then accepts it", async () => {
    const fetchMock = mockDeletionFlow();
    const user = userEvent.setup();
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    await user.click(await screen.findByRole("button", { name: "Request account deletion" }));

    expect(await screen.findByText(/Approval pending/)).toBeInTheDocument();
    const accept = screen.getByRole("button", { name: "Accept deletion request" });
    expect(accept).toBeDisabled();
    await user.click(screen.getByLabelText(/I understand that accepting blocks access/i));
    expect(accept).toBeEnabled();
    await user.click(accept);

    expect(await screen.findByText(/Accepted locally; purge pending/)).toBeInTheDocument();
    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urls).toContain("/api/evidence/deletion-requests");
    expect(urls.some((url) => url.endsWith("/accept"))).toBe(true);
  });

  it("shows the conflict message when a request is already open", async () => {
    mockDeletionFlow({ create: Response.json({ error: "A deletion request is already open for this account." }, { status: 409 }) });
    const user = userEvent.setup();
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    await user.click(await screen.findByRole("button", { name: "Request account deletion" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already open/i);
  });
});

describe("organization account gating", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
  });

  const workspace = {
    id: "10000000-0000-4000-8000-000000000011",
    name: "Personal workspace",
    role: "owner" as const,
  };

  it("shows Workspace & Access only for organization accounts", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/evidence/personal-workspace") return Response.json(workspace);
      if (url === "/api/evidence/me") {
        return Response.json({
          user_id: "user-a",
          account_type: "organization",
          organization_name: "Acme Evidence",
          can_manage_workspace: true,
          workspaces: [workspace],
        });
      }
      return new Response(null, { status: 404 });
    }));
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    expect(await screen.findByText("Workspace & Access")).toBeInTheDocument();
    expect(screen.getByText("Acme Evidence")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create organization account" })).not.toBeInTheDocument();
  });

  it("offers organization creation to individual accounts", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/evidence/personal-workspace") return Response.json(workspace);
      if (url === "/api/evidence/me") {
        return Response.json({
          user_id: "user-a",
          account_type: "individual",
          organization_name: null,
          can_manage_workspace: false,
          workspaces: [workspace],
        });
      }
      return new Response(null, { status: 404 });
    }));
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    expect(await screen.findByRole("button", { name: "Create organization account" })).toBeInTheDocument();
    expect(screen.queryByText("Workspace & Access")).not.toBeInTheDocument();
  });

  it("upgrades an individual account and enables Workspace & Access", async () => {
    let upgraded = false;
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url === "/api/evidence/personal-workspace") return Response.json(workspace);
      if (url === "/api/evidence/me") {
        return Response.json({
          user_id: "user-a",
          account_type: upgraded ? "organization" : "individual",
          organization_name: upgraded ? "Acme Evidence" : null,
          can_manage_workspace: upgraded,
          workspaces: [workspace],
        });
      }
      if (url === "/api/evidence/me/organization") {
        upgraded = true;
        return Response.json({
          user_id: "user-a",
          account_type: "organization",
          organization_name: "Acme Evidence",
          can_manage_workspace: true,
          workspaces: [workspace],
        });
      }
      return new Response(null, { status: 404 });
    }));
    const user = userEvent.setup();
    render(<AuthAccount userId="user-a" email="a@example.test" />);
    await user.type(await screen.findByLabelText("Organization name"), "Acme Evidence");
    await user.click(screen.getByRole("button", { name: "Create organization account" }));
    expect(await screen.findByText("Workspace & Access")).toBeInTheDocument();
    expect(screen.getByText("Acme Evidence")).toBeInTheDocument();
    expect(calls).toContain("/api/evidence/me/organization");
  });
});
