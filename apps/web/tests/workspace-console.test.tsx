import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
const WORKSPACE = { id: WORKSPACE_ID, name: "Team workspace", role: "member" };
const RECORD = {
  id: EVIDENCE_ID,
  workspace_id: WORKSPACE_ID,
  content: "Current workspace evidence",
  source_label: "Release note",
  created_at: "2026-10-31T12:00:00Z",
  updated_at: "2026-10-31T12:00:00Z",
};

function mockProductFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/evidence/me") {
      return Response.json({ user_id: USER_ID, workspaces: [WORKSPACE] });
    }
    if (url.endsWith("/evidence") && init?.method === undefined) {
      return Response.json([RECORD]);
    }
    if (url.endsWith("/evidence") && init?.method === "POST") {
      return Response.json({ ...RECORD, content: "New workspace evidence" }, { status: 201 });
    }
    if (url.endsWith(`/sources/${EVIDENCE_ID}/versions/${EVIDENCE_ID}/content`)) {
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

  it("shows verified membership and live workspace records separately from the synthetic demo", async () => {
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    expect(await screen.findByRole("heading", { name: "Evidence workspace" })).toBeInTheDocument();
    expect(await screen.findByText("Current workspace evidence")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Authorized workspace" })).toHaveValue(WORKSPACE_ID);
    expect(screen.getByText(/These are stored workspace evidence records/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Demo / Synthetic" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("heading", { name: "Connected and pending capabilities" })).toBeInTheDocument();
    expect(screen.getByText("Source relationships")).toBeInTheDocument();
    expect(screen.getAllByText("Demo only")).toHaveLength(2);
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

  it("reads source snapshots through the verified source route and shows the digest", async () => {
    const fetchMock = vi.mocked(fetch);
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    await screen.findByText("Current workspace evidence");
    fireEvent.change(screen.getByLabelText("Source ID"), {
      target: { value: EVIDENCE_ID },
    });
    fireEvent.change(screen.getByLabelText("Version ID"), {
      target: { value: EVIDENCE_ID },
    });
    fireEvent.click(screen.getByRole("button", { name: "Read source" }));

    expect(await screen.findByText("source fixture")).toBeInTheDocument();
    expect(screen.getByText("sha-256=:YWJjZA==:")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/evidence/workspaces/" + WORKSPACE_ID + "/sources/" + EVIDENCE_ID +
        "/versions/" + EVIDENCE_ID + "/content",
      { cache: "no-store" },
    );
  });

  it("fails closed if the service is unavailable and does not show fixture evidence as live", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      if (String(input) === "/api/evidence/me") {
        return Response.json({ user_id: USER_ID, workspaces: [WORKSPACE] });
      }
      return Response.json({ error: "Evidence service is unavailable." }, { status: 503 });
    });
    render(<WorkspaceConsole userId={USER_ID} email="reader@example.test" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Evidence service is unavailable.");
    expect(screen.queryByText("Cedar pilot go-live")).not.toBeInTheDocument();
  });

  it("keeps the workspace layout responsive and source output bounded on narrow screens", () => {
    const styles = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");
    expect(styles).toMatch(/\.workspace-record-layout\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/);
    expect(styles).toMatch(/@media\s*\(max-width:\s*600px\)/);
    expect(styles).toMatch(/\.workspace-source-result pre\s*\{[^}]*max-height:\s*28rem;[^}]*overflow:\s*auto;/);
  });
});
