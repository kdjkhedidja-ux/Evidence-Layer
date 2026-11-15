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
