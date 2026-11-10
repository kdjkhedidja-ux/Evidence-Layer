import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkspaceConsole from "../app/workspace/workspace-console";

const mocks = vi.hoisted(() => ({
  router: { replace: vi.fn(), refresh: vi.fn() },
  unsubscribe: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => mocks.router,
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) =>
    <a href={href} {...props}>{children}</a>,
}));
vi.mock("../lib/supabase/client", () => ({
  createSupabaseBrowserClient: () => ({
    auth: {
      onAuthStateChange: () => ({
        data: { subscription: { unsubscribe: mocks.unsubscribe } },
      }),
      signOut: mocks.signOut,
    },
  }),
}));

const USER_ID = "10000000-0000-4000-8000-000000000001";
const WORKSPACE_ID = "10000000-0000-4000-8000-000000000002";
const EVIDENCE_ID = "10000000-0000-4000-8000-000000000003";
const SOURCE_ID = "10000000-0000-4000-8000-000000000004";
const VERSION_ID = "10000000-0000-4000-8000-000000000005";
const WORKSPACE = { id: WORKSPACE_ID, name: "Team workspace", role: "owner" as const };
const RECORD = {
  id: EVIDENCE_ID,
  workspace_id: WORKSPACE_ID,
  content: "Current workspace evidence",
  source_label: "Release note",
  source_id: null,
  source_version_id: null,
  created_at: "2026-10-31T12:00:00Z",
  updated_at: "2026-10-31T12:00:00Z",
};
const VERSION_SUMMARY = {
  source_id: SOURCE_ID,
  version_id: VERSION_ID,
  state: "published",
  sha256: "a".repeat(64),
  size_bytes: 14,
  created_at: "2026-10-31T12:00:00Z",
};

function mockProductFetch(
  workspace: {
    id: string;
    name: string;
    role: "owner" | "admin" | "member";
    workspace_type?: "personal" | "group";
  } = WORKSPACE,
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/evidence/me") {
      return Response.json({ user_id: USER_ID, workspaces: [workspace] });
    }
    if (url.endsWith("/sources") && init?.method === undefined) {
      return Response.json([VERSION_SUMMARY]);
    }
    if (url.endsWith("/evidence") && init?.method === undefined) {
      return Response.json([RECORD]);
    }
    if (url.endsWith("/evidence") && init?.method === "POST") {
      return Response.json({ ...RECORD, content: "New workspace evidence" }, { status: 201 });
    }
    if (url.endsWith(`/sources/${SOURCE_ID}/versions/${VERSION_ID}/content`)) {
      return new Response("source fixture", {
        headers: {
          "Content-Type": "application/octet-stream",
          "Content-Digest": "sha-256=:YWJjZA==:",
        },
      });
    }
    return Response.json({ error: "missing test response" }, { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("workspace product console", () => {
  beforeEach(() => {
    mocks.router.replace.mockReset();
    mocks.router.refresh.mockReset();
    mocks.unsubscribe.mockReset();
    mocks.signOut.mockReset();
    mockProductFetch();
  });

  it("shows verified membership and truthful workspace capability states", async () => {
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    expect(await screen.findByRole("heading", { name: "Evidence workspace" })).toBeInTheDocument();
    expect(await screen.findByText("Current workspace evidence")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Authorized workspace" })).toHaveValue(WORKSPACE_ID);
    expect(screen.getByText("Not exposed by the membership API")).toBeInTheDocument();
    expect(screen.getByText("Owner", { exact: true })).toBeInTheDocument();
    expect(screen.getByText(/These are stored workspace evidence records/)).toBeInTheDocument();
    expect(screen.getByText("1 record")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Evidence Explorer" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Relationships" })).toHaveAttribute("href", "/relationships");
    expect(screen.getByRole("heading", { name: "Capability Catalog" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Evidence Relationships" })).toBeInTheDocument();
    expect(screen.queryByText("Demo only")).not.toBeInTheDocument();
  });

  it("shows contract-based feature states and links only to existing destinations", async () => {
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await screen.findByText("Current workspace evidence");

    const catalog = screen.getByRole("region", { name: "Capability Catalog" });
    const links = within(catalog).getAllByRole("link");
    const existingDestinations = new Set([
      "/workspace#workspace-session",
      "/workspace#account-profile",
      "/workspace#membership-heading",
      "/workspace#session-management",
      "/mfa",
      "/?account=1#account-organization-title",
      "/#explorer",
      "/workspace#records-heading",
      "/#reading-guide",
      "/workspace#source-read-heading",
      "/relationships",
      "/#verification",
      "/cost-performance",
      "/#settings-access",
    ]);
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      const href = link.getAttribute("href") ?? "";
      expect(existingDestinations.has(href)).toBe(true);
      expect(link).toHaveTextContent("Open existing page");
      if (href.startsWith("/workspace#")) {
        expect(document.getElementById(href.split("#")[1] ?? "")).not.toBeNull();
      }
    }
    // Groups are collapsed to their first card; the rest appear on demand.
    const groupToggles = within(catalog).getAllByRole("button");
    expect(groupToggles.length).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: "Personal API Keys" })).not.toBeInTheDocument();

    const settingsGroup = screen.getByRole("heading", { name: "Settings & Access" }).closest("section");
    expect(settingsGroup).not.toBeNull();
    if (!settingsGroup) throw new Error("Settings & Access group is missing");
    fireEvent.click(within(settingsGroup).getByRole("button", { name: /Show \d+ more/ }));

    const keysFeature = screen.getByRole("heading", { name: "Personal API Keys" }).closest("li");
    expect(keysFeature).not.toBeNull();
    if (!keysFeature) throw new Error("Personal API Keys capability is missing its list item");
    expect(within(keysFeature).getByText("Partially available")).toBeInTheDocument();
    expect(keysFeature).toHaveTextContent(/read-only keys/i);
  });

  it("keeps evidence mutations unavailable in the member UI", async () => {
    mockProductFetch({ ...WORKSPACE, role: "member" });
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await screen.findByText("Current workspace evidence");
    expect(screen.queryByLabelText("Evidence content")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(screen.getByText("Read-only member access")).toBeInTheDocument();
  });

  it("lists an organization admin group workspace with edit access", async () => {
    mockProductFetch({ ...WORKSPACE, name: "Group workspace", role: "admin", workspace_type: "group" });
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await screen.findByText("Current workspace evidence");
    expect(screen.getByRole("combobox", { name: "Authorized workspace" })).toHaveValue(WORKSPACE_ID);
    expect(
      within(screen.getByRole("combobox", { name: "Authorized workspace" })).getByRole(
        "option",
        { name: "Group workspace · admin" },
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Admin", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Type:").closest("p")).toHaveTextContent("Group workspace");
    expect(screen.getByLabelText("Evidence content")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("shows the verified session expiry and signs out every session", async () => {
    mocks.signOut.mockResolvedValue({ error: null });
    render(
      <WorkspaceConsole
        userId={USER_ID}
        email="reader@example.test"
        sessionExpiresAt={1893456000}
      />,
    );
    await screen.findByText("Current workspace evidence");
    expect(screen.getByRole("heading", { name: "Session" })).toBeInTheDocument();
    expect(screen.getByText(/Session expires:/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign out everywhere" }));
    await waitFor(() => {
      expect(mocks.signOut).toHaveBeenCalledWith({ scope: "global" });
    });
    expect(mocks.router.replace).toHaveBeenCalledWith("/login?reason=signed-out");
  });

  it("creates evidence using the current selected workspace path", async () => {
    const fetchMock = vi.mocked(fetch);
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await screen.findByText("Current workspace evidence");
    fireEvent.change(screen.getByLabelText("Evidence content"), {
      target: { value: "New workspace evidence" },
    });
    fireEvent.change(screen.getByLabelText("Source label (optional)"), {
      target: { value: "Planning note" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add evidence" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/evidence/workspaces/${WORKSPACE_ID}/evidence`,
        expect.objectContaining({ method: "POST", cache: "no-store" }),
      );
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Evidence record created.");
  });

  it("lists workspace source versions and reads one through the verified route", async () => {
    const fetchMock = vi.mocked(fetch);
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await screen.findByText("Current workspace evidence");
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/evidence/workspaces/${WORKSPACE_ID}/sources`,
      { cache: "no-store" },
    );
    expect(await screen.findByText(/Version 10000000/)).toBeInTheDocument();
    expect(screen.getAllByText(/Published/).length).toBeGreaterThanOrEqual(1);

    fireEvent.click(screen.getByRole("button", { name: "Read source" }));

    expect(await screen.findByText("source fixture")).toBeInTheDocument();
    expect(screen.getByText("sha-256=:YWJjZA==:")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/evidence/workspaces/" + WORKSPACE_ID + "/sources/" + SOURCE_ID +
        "/versions/" + VERSION_ID + "/content",
      { cache: "no-store" },
    );
  });

  it("shows an honest empty state when no sources are visible", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/evidence/me") {
        return Response.json({ user_id: USER_ID, workspaces: [WORKSPACE] });
      }
      if (url.endsWith("/sources")) {
        return Response.json([]);
      }
      if (url.endsWith("/evidence")) {
        return Response.json([RECORD]);
      }
      return Response.json({ error: "missing test response" }, { status: 404 });
    });
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    expect(
      await screen.findByText("No verified sources are available to your account in this workspace."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Read source" })).not.toBeInTheDocument();
  });

  it("fails closed if the service is unavailable and does not show fixture evidence as live", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      if (String(input) === "/api/evidence/me") {
        return Response.json({ user_id: USER_ID, workspaces: [WORKSPACE] });
      }
      return Response.json({ error: "Evidence service is unavailable." }, { status: 503 });
    });
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThanOrEqual(2);
    });
    expect(
      screen.getAllByRole("alert").some((alert) =>
        alert.textContent?.includes("Evidence service is unavailable."),
      ),
    ).toBe(true);
    expect(screen.queryByText("Cedar pilot go-live")).not.toBeInTheDocument();
    expect(screen.queryByText("source fixture")).not.toBeInTheDocument();
  });

  it("does not present an evidence read failure as zero records", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/evidence/me") {
        return Response.json({ user_id: USER_ID, workspaces: [WORKSPACE] });
      }
      if (url.endsWith("/evidence")) {
        return Response.json({ error: "Evidence service is unavailable." }, { status: 503 });
      }
      if (url.endsWith("/sources")) return Response.json([]);
      return Response.json({ error: "missing test response" }, { status: 404 });
    });
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await waitFor(() => {
      expect(screen.getAllByText("Evidence service is unavailable.").length).toBeGreaterThan(0);
    });
    expect(screen.getByText("Unavailable", { selector: ".workspace-count" })).toBeInTheDocument();
    expect(screen.queryByText("0 records")).not.toBeInTheDocument();
    expect(
      screen.queryByText("No evidence records are stored in this workspace."),
    ).not.toBeInTheDocument();
  });

  it("keeps the workspace layout responsive and source output bounded on narrow screens", () => {
    const styles = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");
    expect(styles).toMatch(/\.workspace-record-layout\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/);
    expect(styles).toMatch(/@media\s*\(max-width:\s*600px\)/);
    expect(styles).toMatch(/\.workspace-source-result pre\s*\{[^}]*max-height:\s*28rem;[^}]*overflow:\s*auto;/);
  });
});
