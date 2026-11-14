import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SiteHeader from "../components/site-header";

const mocks = vi.hoisted(() => ({
  getSession: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock("../lib/supabase/client", () => ({
  createSupabaseBrowserClient: () => ({
    auth: { getSession: mocks.getSession, signOut: mocks.signOut },
  }),
}));

describe("SiteHeader", () => {
  beforeEach(() => {
    mocks.getSession.mockReset().mockResolvedValue({ data: { session: null } });
    mocks.signOut.mockReset().mockResolvedValue({ error: null });
    window.history.replaceState({}, "", "/documentation");
  });

  it("uses one menu with the same navigation on every page", () => {
    render(<SiteHeader />);
    const toggle = screen.getByRole("button", { name: "Menu" });
    expect(toggle).toHaveAttribute("aria-controls", "site-menu");
    fireEvent.click(toggle);
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Workspace",
      "Telemetry",
      "Evidence",
      "Relationships",
      "Connect an agent",
      "Documentation",
      "Evaluation · Benchmarks",
      "Cost & Performance",
      "Privacy",
      "Terms",
      "Data Deletion",
      "Account",
    ]);
    expect(within(nav).getByRole("link", { name: "Documentation" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Account" })).toHaveAttribute("href", "/account");
  });

  it("shows the signed-in account and performs a local sign out", async () => {
    mocks.getSession.mockResolvedValue({ data: { session: { user: { email: "reader@example.test" } } } });
    render(<SiteHeader />);
    fireEvent.click(screen.getByRole("button", { name: "Menu" }));
    expect(await screen.findByText("reader@example.test")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(mocks.signOut).toHaveBeenCalledWith({ scope: "local" });
  });

  it("does not call Supabase when the account is provided by the page", () => {
    render(<SiteHeader accountEmail="known@example.test" />);
    expect(mocks.getSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Menu" }));
    expect(screen.getByText("known@example.test")).toBeInTheDocument();
  });
});
