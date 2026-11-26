import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ConnectorSources from "../components/connector-sources";

describe("connector OAuth feedback", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/workspace?connector=google_drive&status=retry");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json([])));
  });

  it("explains that the connector must be retried after MFA verification", async () => {
    render(<ConnectorSources />);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Two-factor authentication is verified. Retry connecting Google Drive.",
    );
  });

  it("shows a load failure instead of reporting that no source is connected", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network unavailable")));
    render(<ConnectorSources />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Connected sources are unavailable right now.",
    );
    expect(screen.queryByText("No source is connected.")).not.toBeInTheDocument();
  });
});

describe("files not indexed", () => {
  const connection = {
    connection_id: "10000000-0000-4000-8000-000000000001",
    provider: "github",
    workspace_id: null,
    account_email: null,
    status: "connected",
    last_sync_at: null,
    last_error_category: null,
  };

  beforeEach(() => {
    window.history.replaceState({}, "", "/workspace?connector=github");
  });

  it("shows the owner each recorded file with its reason", async () => {
    const fetchMock = vi.fn(async (url: string) =>
      url.endsWith("/not-indexed")
        ? Response.json([
            { repository: "acme/docs", path: "data/huge.json", reason: "too_large", size_bytes: 3145728 },
            { repository: "acme/docs", path: "docs/logo.png", reason: "binary", size_bytes: 10 },
          ])
        : Response.json([connection]),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<ConnectorSources />);

    (await screen.findByRole("button", { name: "Files not indexed" })).click();

    expect(await screen.findByText("acme/docs/data/huge.json")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Files not indexed" })).toBeInTheDocument();
    expect(screen.getByText(/larger than 2 MiB/)).toBeInTheDocument();
    expect(screen.getByText(/not a text file/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/evidence/connectors/${connection.connection_id}/not-indexed`,
      { cache: "no-store" },
    );
  });

  it("reports an unavailable list instead of claiming everything is indexed", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      url.endsWith("/not-indexed") ? new Response(null, { status: 503 }) : Response.json([connection]),
    ));
    render(<ConnectorSources />);

    (await screen.findByRole("button", { name: "Files not indexed" })).click();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The list of files not indexed is unavailable right now.",
    );
    expect(screen.queryByText("Every listed file of this source is indexed.")).not.toBeInTheDocument();
  });
});
