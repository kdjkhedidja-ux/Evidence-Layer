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
  storageUpload: vi.fn(),
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
      getSession: async () => ({ data: { session: null }, error: null }),
      signOut: mocks.signOut,
    },
    storage: {
      from: () => ({ upload: mocks.storageUpload }),
    },
  }),
}));

const USER_ID = "10000000-0000-4000-8000-000000000001";
const WORKSPACE_ID = "10000000-0000-4000-8000-000000000002";
const EVIDENCE_ID = "10000000-0000-4000-8000-000000000003";
const RELATED_EVIDENCE_ID = "10000000-0000-4000-8000-000000000006";
const RELATION_ID = "10000000-0000-4000-8000-000000000007";
const SOURCE_ID = "10000000-0000-4000-8000-000000000004";
const VERSION_ID = "10000000-0000-4000-8000-000000000005";
const MEMBER_ID = "10000000-0000-4000-8000-000000000006";
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
    if (url.endsWith("/invitations") && init?.method === "POST") {
      return Response.json({
        invitation_id: "10000000-0000-4000-8000-000000000007",
        email: "member@example.test",
        role: "member",
        created_at: "2026-11-15T00:00:00Z",
        expires_at: "2026-11-22T00:00:00Z",
        invite_token: "synthetic-invite-token-value-1234",
      }, { status: 201 });
    }
    if (url.endsWith(`/sources/${SOURCE_ID}/grants`) && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as { principal_id: string };
      return Response.json({
        principal_id: body.principal_id,
        grant_type: "read",
        granted_by: USER_ID,
        created_at: "2026-11-15T00:00:00Z",
      }, { status: 201 });
    }
    if (url.endsWith("/sources") && init?.method === "POST") {
      return Response.json({
        source_id: SOURCE_ID,
        version_id: VERSION_ID,
        state: "staged",
        sha256: "a".repeat(64),
        size_bytes: 5,
        bucket: "source-snapshots",
        object_key: `workspace/${WORKSPACE_ID}/source/${SOURCE_ID}/version/${VERSION_ID}`,
        created_at: "2026-11-15T00:00:00Z",
      }, { status: 201 });
    }
    if (url.endsWith("/evidence") && init?.method === undefined) {
      return Response.json([RECORD]);
    }
    if (url.endsWith("/evidence") && init?.method === "POST") {
      return Response.json({ ...RECORD, content: "New workspace evidence" }, { status: 201 });
    }
    if (url.endsWith("/task-context") && init?.method === "POST") {
      return Response.json({
        task: "Review the release",
        expected_outcome: "Identify risks and mitigations",
        context_requirements: "Focus on deployment",
        retrieval: {
          status: "ok",
          query: "Review the release\nIdentify risks and mitigations\nFocus on deployment",
          workspace_id: WORKSPACE_ID,
          selected_count: 2,
          used_tokens: 18,
          used_chars: 72,
          token_estimator: "chars_div_4",
          scoring_provider: "lexical_stopword_filtered",
          scoring_fallback_reason: null,
          evidence: [
            {
              evidence_id: EVIDENCE_ID,
              content: "Release checklist and deployment evidence.",
              source_label: "release.md",
              source_id: null,
              source_version_id: null,
              score: 0.8,
              chunk_id: `${EVIDENCE_ID}:0:40`,
              token_count: 10,
              char_count: 40,
              path: "release.md",
              title: "Release",
            },
            {
              evidence_id: RELATED_EVIDENCE_ID,
              content: "Rollback steps and owner approvals.",
              source_label: "rollback.md",
              source_id: null,
              source_version_id: null,
              score: 0.6,
              chunk_id: `${RELATED_EVIDENCE_ID}:0:32`,
              token_count: 8,
              char_count: 32,
              path: "rollback.md",
              title: "Rollback",
            },
          ],
        },
        relationships: [
          {
            id: RELATION_ID,
            evidence_id_a: EVIDENCE_ID,
            evidence_id_b: RELATED_EVIDENCE_ID,
            relation_type: "contextualizes",
            rationale: "The rollback procedure provides operational context.",
            provenance_note: "Reviewed together by the workspace owner.",
          },
        ],
      });
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
    mocks.storageUpload.mockReset();
    mocks.storageUpload.mockResolvedValue({ data: {}, error: null });
    mockProductFetch();
  });

  it("shows verified membership and truthful workspace capability states", async () => {
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    expect(await screen.findByRole("heading", { name: "Evidence workspace" })).toBeInTheDocument();
    expect(await screen.findByText("Current workspace evidence")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Authorized workspace" })).toHaveValue(WORKSPACE_ID);
    expect(screen.getByText("Not exposed by the membership API")).toBeInTheDocument();
    expect(screen.getByText("Owner", { exact: true })).toBeInTheDocument();
    expect(screen.getByText(/Connector sync currently imports into your personal workspace/)).toBeInTheDocument();
    expect(screen.getByText("1 record")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Evidence" })).toHaveAttribute("href", "/#explorer");
    expect(screen.getByRole("link", { name: "Relationships" })).toHaveAttribute("href", "/relationships");
    expect(screen.getByText("Full capability reference (advanced)")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Task outcomes and usage" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Evidence Relationships" })).toBeInTheDocument();
    expect(screen.queryByText("Demo only")).not.toBeInTheDocument();
  });

  it("puts source sync before agent setup", async () => {
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    expect(await screen.findByRole("heading", { name: "Remote MCP" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Connected sources" })).toBeInTheDocument();
    const headings = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
    expect(headings.indexOf("Connected sources")).toBeLessThan(headings.indexOf("Remote MCP"));
    expect(screen.getByRole("link", { name: "Connect an agent" })).toHaveAttribute(
      "href",
      "/workspace#agent-connection",
    );
    expect(screen.getByText(/does not yet run agent tasks or collect the model's token usage/i)).toBeInTheDocument();
  });

  it("uses the task and expected outcome to prepare bounded, linked evidence context", async () => {
    const fetchMock = mockProductFetch();
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await screen.findByText("Current workspace evidence");
    fireEvent.change(screen.getByLabelText("What should the agent do?"), {
      target: { value: "Review the release" },
    });
    fireEvent.change(screen.getByLabelText("What should a successful result include?"), {
      target: { value: "Identify risks and mitigations" },
    });
    fireEvent.change(screen.getByLabelText("What context should EL prioritize? (optional)"), {
      target: { value: "Focus on deployment" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Prepare evidence context" }));

    expect(await screen.findByText("Release checklist and deployment evidence.")).toBeInTheDocument();
    expect(screen.getByText("The rollback procedure provides operational context.")).toBeInTheDocument();
    expect(screen.getByText(/18 estimated evidence tokens/)).toBeInTheDocument();
    const request = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/task-context"));
    expect(request).toBeDefined();
    expect(request?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        task: "Review the release",
        expected_outcome: "Identify risks and mitigations",
        context_requirements: "Focus on deployment",
        top_k: 5,
        max_tokens: 1200,
        max_chars: 6000,
        use_jev: true,
      }),
    });
    expect(screen.getByText(/sent to JEV for scoring/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /Score with JEV/ }));
    fireEvent.click(screen.getByRole("button", { name: "Prepare evidence context" }));
    await waitFor(() => {
      const taskRequests = fetchMock.mock.calls.filter(([input]) =>
        String(input).endsWith("/task-context"),
      );
      expect(taskRequests).toHaveLength(2);
      expect(taskRequests[1]?.[1]?.body).toContain('"use_jev":false');
    });

  });

  it("creates a one-time invitation link", async () => {
    const fetchMock = mockProductFetch();
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await screen.findByLabelText("Invitee email");

    fireEvent.change(screen.getByLabelText("Invitee email"), {
      target: { value: "member@example.test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create invitation" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/invitations"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(
      await screen.findByDisplayValue(/synthetic-invite-token-value-1234/),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/Share the link now/),
    ).toBeInTheDocument();
  });

  it("grants read access to a workspace member", async () => {
    const fetchMock = mockProductFetch();
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await screen.findByLabelText("Member user ID");

    fireEvent.change(screen.getByLabelText("Source"), {
      target: { value: SOURCE_ID },
    });
    fireEvent.change(screen.getByLabelText("Member user ID"), {
      target: { value: MEMBER_ID },
    });
    fireEvent.click(screen.getByRole("button", { name: "Grant read" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(`/sources/${SOURCE_ID}/grants`),
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(
      await screen.findByText(/Read access granted/),
    ).toBeInTheDocument();
  });

  it("links to the telemetry page from the workspace header", async () => {
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    expect(screen.getByRole("link", { name: "Telemetry" })).toHaveAttribute("href", "/workspace/telemetry");
  });

  it("stages a source version and uploads the object as the owner", async () => {
    const fetchMock = mockProductFetch();
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await screen.findByText("Current workspace evidence");

    fireEvent.change(screen.getByLabelText("Source content"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Stage version" }));

    await waitFor(() => expect(mocks.storageUpload).toHaveBeenCalledTimes(1));
    const call = mocks.storageUpload.mock.calls[0] as [string, Blob, Record<string, unknown>];
    expect(call[0]).toBe(
      `workspace/${WORKSPACE_ID}/source/${SOURCE_ID}/version/${VERSION_ID}`,
    );
    expect(call[1]).toBeInstanceOf(Blob);
    expect(call[2]).toMatchObject({
      upsert: false,
      contentType: "application/octet-stream",
    });
    const request = fetchMock.mock.calls.find(
      ([input, init]) => String(input).endsWith("/sources") && init?.method === "POST",
    );
    expect(request).toBeDefined();
    expect(request?.[1]).toMatchObject({
      body: JSON.stringify({ content: "hello" }),
    });
    expect(await screen.findByText(/pending verifier publication/)).toBeInTheDocument();
  });

  it("offers navigation and a local sign out from the header menu", async () => {
    mocks.signOut.mockResolvedValue({ error: null });
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    const toggle = screen.getByRole("button", { name: "Menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", "site-menu");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Close" })).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(mocks.signOut).toHaveBeenCalledWith({ scope: "local" }));
  });

  it("shows contract-based feature states and links only to existing destinations", async () => {
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await screen.findByText("Current workspace evidence");
    fireEvent.click(screen.getByText("Full capability reference (advanced)"));

    const catalog = screen.getByRole("region", { name: "Capability Catalog" });
    const links = within(catalog).getAllByRole("link");
    const existingDestinations = new Set([
      "/workspace#membership-heading",
      "/?account=1#account-panel",
      "/?account=1#account-profile",
      "/?account=1#account-session-title",
      "/mfa",
      "/?account=1#account-organization-title",
      "/#explorer",
      "/workspace#records-heading",
      "/workspace#source-read-heading",
      "/workspace#task-context",
      "/workspace/telemetry",
      "/account",
      "/relationships",
      "/evaluation",
      "/cost-performance",
      "/workspace#membership-heading",
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
    expect(screen.getByText(WORKSPACE_ID)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(screen.getByText("Type:").closest("p")).toHaveTextContent("Group workspace");
    expect(screen.getByLabelText("Evidence content")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("keeps account profile and session controls out of the workspace page", async () => {
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await screen.findByText("Current workspace evidence");
    expect(screen.queryByRole("heading", { name: "Account profile", level: 2 })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out everywhere" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Session", level: 2 })).not.toBeInTheDocument();
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
