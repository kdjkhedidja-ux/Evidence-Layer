import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import RemoteMcp from "../components/remote-mcp";
import { remoteMcpEnabled, remoteMcpWorkspaceUrl } from "../lib/remote-mcp";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("Remote MCP flags and URL", () => {
  it("defaults to off and requires the exact flag", () => {
    expect(remoteMcpEnabled({})).toBe(false);
    expect(remoteMcpEnabled({ flag: "false" })).toBe(false);
    expect(remoteMcpEnabled({ flag: "TRUE" })).toBe(false);
    expect(remoteMcpEnabled({ flag: "true" })).toBe(true);
  });

  it("builds a workspace-scoped hosted URL", () => {
    expect(remoteMcpWorkspaceUrl({ baseUrl: "https://api.example/mcp/", workspaceId: "ws-1" })).toBe(
      "https://api.example/mcp/workspaces/ws-1/",
    );
    expect(remoteMcpWorkspaceUrl({ baseUrl: "", workspaceId: "ws-1" })).toBeNull();
    expect(remoteMcpWorkspaceUrl({ baseUrl: "https://api.example/mcp", workspaceId: "" })).toBeNull();
  });
});

describe("RemoteMcp", () => {
  it("shows a coming-soon state when the independent flag is off", () => {
    vi.stubEnv("NEXT_PUBLIC_REMOTE_MCP_ENABLED", "false");
    render(<RemoteMcp workspace={{ id: "ws-1", name: "Personal" }} />);
    expect(screen.getByRole("heading", { name: "Remote MCP" })).toBeInTheDocument();
    expect(screen.getByText(/not enabled in this environment/i)).toBeInTheDocument();
    expect(screen.queryByText(/https:\/\/api\.example/)).not.toBeInTheDocument();
  });

  it("shows the hosted URL and copyable client config when enabled", () => {
    vi.stubEnv("NEXT_PUBLIC_REMOTE_MCP_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_REMOTE_MCP_URL", "https://api.example/mcp");
    render(<RemoteMcp workspace={{ id: "ws-1", name: "Personal" }} />);
    expect(screen.getByText("https://api.example/mcp/workspaces/ws-1/")).toBeInTheDocument();
    expect(screen.getAllByText(/YOUR_PERSONAL_API_KEY/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Copy URL" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy config" })).toBeInTheDocument();
  });

  it("asks for a workspace when none is selected", () => {
    vi.stubEnv("NEXT_PUBLIC_REMOTE_MCP_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_REMOTE_MCP_URL", "https://api.example/mcp");
    render(<RemoteMcp workspace={null} />);
    expect(screen.getByText(/select a workspace/i)).toBeInTheDocument();
  });
});
