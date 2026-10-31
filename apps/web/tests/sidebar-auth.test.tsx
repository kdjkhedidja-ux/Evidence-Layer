import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EvidenceExplorer from "../app/evidence-explorer";

vi.mock("next/link", () => ({
  default: ({ href, children, "aria-label": ariaLabel, className, title }: {
    href: string;
    children: ReactNode;
    "aria-label"?: string;
    className?: string;
    title?: string;
  }) => <a href={href} aria-label={ariaLabel} className={className} title={title}>{children}</a>,
}));

vi.mock("../components/auth-account", () => ({
  default: ({ email }: { userId: string; email: string }) => (
    <section id="account-panel" aria-label="Account workspace"><p>{email}</p></section>
  ),
}));

const styles = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");

describe("sidebar account control", () => {
  beforeEach(() => {
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
  });

  it("matches the theme SVG size and stroke style and links guests to sign in", () => {
    render(<EvidenceExplorer account={null} />);

    const signIn = screen.getByRole("link", { name: "Sign in" });
    expect(signIn).toHaveAttribute("href", "/login");
    const accountIcon = signIn.querySelector("svg");
    const themeIcon = document.querySelector(".theme-toggle .theme-icon");
    expect(accountIcon).not.toBeNull();
    expect(themeIcon).not.toBeNull();
    for (const attribute of ["viewBox", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"]) {
      expect(accountIcon?.getAttribute(attribute)).toBe(themeIcon?.getAttribute(attribute));
    }
    expect(accountIcon?.getAttribute("viewBox")).toBe("0 0 24 24");
    expect(styles).toMatch(/\.account-icon\s*\{[\s\S]*?width:\s*21px;[\s\S]*?height:\s*21px;/);
    expect(styles).toMatch(/\.theme-icon\s*\{[\s\S]*?width:\s*21px;[\s\S]*?height:\s*21px;/);
    expect(styles).toMatch(/\.account-toggle:focus-visible\s*\{[\s\S]*?outline:\s*2px solid var\(--sidebar-focus\)/);
    expect(styles).toMatch(/:root\[data-theme="dark"\] \.account-toggle\s*\{[\s\S]*?color:\s*var\(--mint\)/);
    expect(styles).toMatch(/:root\[data-theme="dark"\] \.theme-toggle\s*\{[\s\S]*?color:\s*var\(--mint\)/);
  });

  it("links the workspace navigation to all public information pages", () => {
    render(<EvidenceExplorer account={null} />);
    const navigation = within(screen.getByRole("navigation", { name: "Primary navigation" }));

    for (const [name, href] of [
      ["Documentation", "/documentation"],
      ["Cost & Performance", "/cost-performance"],
      ["Privacy Policy", "/privacy"],
      ["Terms of Use", "/terms"],
    ]) {
      expect(navigation.getByRole("link", { name })).toHaveAttribute("href", href);
    }
  });

  it("opens the signed-in account panel from a keyboard-accessible button", async () => {
    const user = userEvent.setup();
    render(<EvidenceExplorer account={{ id: "user-a", email: "a@example.test" }} />);

    const toggle = screen.getByRole("button", { name: "Open account" });
    expect(toggle).toHaveAttribute("aria-controls", "account-panel");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    toggle.focus();
    await user.keyboard("{Enter}");

    expect(screen.getByRole("button", { name: "Close account" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("region", { name: "Account workspace" })).toHaveTextContent("a@example.test");
  });
});
