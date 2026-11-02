import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LiveEvidenceExplorer from "../app/live-evidence-explorer";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) =>
    <a href={href} {...props}>{children}</a>,
}));
vi.mock("../components/auth-account", () => ({ default: () => <div>Account settings</div> }));
vi.mock("../app/theme-toggle", () => ({ default: () => <button type="button">Theme</button> }));

const USER_ID = "10000000-0000-4000-8000-000000000001";
const WORKSPACE_ID = "a1000000-0000-4000-8000-000000000001";
const RECORD_A_ID = "e1000000-0000-4000-8000-000000000001";
const RECORD_B_ID = "e2000000-0000-4000-8000-000000000002";
const WORKSPACE = { id: WORKSPACE_ID, name: "Research workspace", role: "owner" as const };
const RECORD_A = {
  id: RECORD_A_ID,
  workspace_id: WORKSPACE_ID,
  content: "Stored source claim alpha.",
  source_label: "Minutes",
  source_id: null,
  source_version_id: null,
  created_at: "2026-11-02T12:00:00Z",
  updated_at: "2026-11-02T12:00:00Z",
};
const RECORD_B = { ...RECORD_A, id: RECORD_B_ID, content: "Stored source claim beta.", source_label: "Report" };

function liveRelationship() {
  return {
    id: "f1000000-0000-4000-8000-000000000001",
    workspace_id: WORKSPACE_ID,
    evidence_id_a: RECORD_A_ID,
    evidence_id_b: RECORD_B_ID,
    relation_type: "contradicts",
    rationale: "The two stored claims disagree.",
    provenance_note: "Compared the original records.",
    created_by: USER_ID,
    created_at: "2026-11-02T12:00:00Z",
    updated_by: USER_ID,
    updated_at: "2026-11-02T12:00:00Z",
  };
}

describe("live Evidence Explorer", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/evidence/me") {
        return Response.json({ user_id: USER_ID, workspaces: [WORKSPACE] });
      }
      if (url.endsWith("/evidence")) return Response.json([RECORD_A, RECORD_B]);
      if (url.endsWith("/sources")) return Response.json([]);
      if (url.endsWith("/relationships")) return Response.json([]);
      return Response.json({ error: "missing test response" }, { status: 404 });
    }));
  });

  it("uses only authenticated workspace records and filters their actual contents", async () => {
    render(<LiveEvidenceExplorer account={{ id: USER_ID, email: "reader@example.test" }} />);
    expect(await screen.findAllByText("Stored source claim alpha.")).toHaveLength(3);
    expect(screen.getByText("Stored source claim beta.")).toBeInTheDocument();
    expect(screen.queryByText(/Cedar pilot|EV-SYN|Synthetic sample/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Interpretation and unresolved questions are not generated/)).toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: "Search actual evidence records" }), {
      target: { value: "beta" },
    });
    expect(screen.queryByText("Stored source claim alpha.")).not.toBeInTheDocument();
    expect(screen.getAllByText("Stored source claim beta.")).toHaveLength(3);
  });

  it("keeps one primary navigation destination for every existing product page and section", async () => {
    render(<LiveEvidenceExplorer account={{ id: USER_ID, email: "reader@example.test" }} />);
    await screen.findAllByText("Stored source claim alpha.");

    const navigationRoot = screen.getByRole("navigation", { name: "Product navigation" });
    const navigation = within(navigationRoot);
    const links = navigation.getAllByRole("link");
    const destinations = links.map((link) => link.getAttribute("href"));
    expect(new Set(destinations).size).toBe(destinations.length);
    expect(links.map((link) => link.querySelector("span")?.textContent)).toEqual([
      "Workspace",
      "Evidence Explorer",
      "Relationships",
      "Reading Guide",
      "Verification",
      "Evaluation",
      "Cost & Performance",
      "Integrations",
      "Settings & Access",
      "Documentation",
    ]);
    expect(destinations).toEqual([
      "/workspace",
      "#explorer",
      "/relationships",
      "#reading-guide",
      "#verification",
      "#evaluation",
      "/cost-performance",
      "#integrations",
      "#settings-access",
      "/documentation",
    ]);
    for (const destination of destinations) {
      if (destination?.startsWith("#")) {
        expect(document.getElementById(destination.slice(1))).not.toBeNull();
      }
    }
    const validStatuses = new Set([
      "Connected",
      "Partially available",
      "Evidence-grounded",
      "Historical",
      "Pending",
      "Unavailable",
      "Limited / Read only",
    ]);
    for (const status of navigationRoot.querySelectorAll("small")) {
      expect(validStatuses.has(status.textContent ?? "")).toBe(true);
    }
  });

  it("keeps relationship-page links pointed at the single existing destinations", async () => {
    render(
      <LiveEvidenceExplorer
        relationshipsOnly
        account={{ id: USER_ID, email: "reader@example.test" }}
      />,
    );
    expect(await screen.findByRole("heading", { name: "Evidence Relationships" })).toBeInTheDocument();

    const navigation = within(screen.getByRole("navigation", { name: "Product navigation" }));
    const links = navigation.getAllByRole("link");
    const destinations = links.map((link) => link.getAttribute("href"));
    expect(new Set(destinations).size).toBe(destinations.length);
    expect(destinations).toEqual([
      "/workspace",
      "/",
      "#relationships",
      "/#reading-guide",
      "/#verification",
      "/#evaluation",
      "/cost-performance",
      "/#integrations",
      "/#settings-access",
      "/documentation",
    ]);
  });

  it("fails closed when evidence service is unavailable instead of using synthetic fixtures", async () => {
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/evidence/me") return Response.json({ user_id: USER_ID, workspaces: [WORKSPACE] });
      if (url.endsWith("/evidence")) return Response.json({ error: "private details" }, { status: 503 });
      if (url.endsWith("/sources")) return Response.json([]);
      if (url.endsWith("/relationships")) return Response.json([]);
      return Response.json({}, { status: 404 });
    });
    render(<LiveEvidenceExplorer account={{ id: USER_ID, email: "reader@example.test" }} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/does not substitute sample records/i);
    expect(screen.queryByText(/Cedar pilot|EV-SYN|Synthetic sample/i)).not.toBeInTheDocument();
  });

  it("shows a useful empty state when a real workspace contains no evidence", async () => {
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/evidence/me") return Response.json({ user_id: USER_ID, workspaces: [WORKSPACE] });
      if (url.endsWith("/evidence")) return Response.json([]);
      if (url.endsWith("/sources")) return Response.json([]);
      if (url.endsWith("/relationships")) return Response.json([]);
      return Response.json({}, { status: 404 });
    });
    render(<LiveEvidenceExplorer account={{ id: USER_ID, email: "reader@example.test" }} />);
    expect(await screen.findByRole("heading", { name: "No evidence records yet" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Open Workspace" })[0]).toHaveAttribute("href", "/workspace");
  });

  it("submits a documented relationship only from the real workspace list", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/evidence/me") return Response.json({ user_id: USER_ID, workspaces: [WORKSPACE] });
      if (url.endsWith("/evidence")) return Response.json([RECORD_A, RECORD_B]);
      if (url.endsWith("/sources")) return Response.json([]);
      if (url.endsWith("/relationships") && init?.method === "POST") return Response.json(liveRelationship(), { status: 201 });
      if (url.endsWith("/relationships")) return Response.json([liveRelationship()]);
      return Response.json({}, { status: 404 });
    });
    render(<LiveEvidenceExplorer account={{ id: USER_ID, email: "reader@example.test" }} />);
    await screen.findAllByText("Stored source claim alpha.");
    fireEvent.change(screen.getByLabelText("Evidence A"), { target: { value: RECORD_A_ID } });
    fireEvent.change(screen.getByLabelText("Evidence B"), { target: { value: RECORD_B_ID } });
    fireEvent.change(screen.getByLabelText("Rationale"), { target: { value: "The two stored claims disagree." } });
    fireEvent.change(screen.getByLabelText("Provenance / review basis"), { target: { value: "Compared the original records." } });
    fireEvent.click(screen.getByRole("button", { name: "Save relationship" }));
    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      expect(createCall).toBeDefined();
      expect(JSON.parse(String(createCall?.[1]?.body))).toMatchObject({
        evidence_id_a: RECORD_A_ID,
        evidence_id_b: RECORD_B_ID,
        rationale: "The two stored claims disagree.",
      });
    });
  });

  it("requires authentication before displaying any workspace data", async () => {
    render(<LiveEvidenceExplorer account={null} />);
    expect(await screen.findByRole("heading", { name: "Sign in to inspect evidence" })).toBeInTheDocument();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});
