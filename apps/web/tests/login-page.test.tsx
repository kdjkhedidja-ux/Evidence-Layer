import { describe, expect, it } from "vitest";
import type { ReactElement } from "react";
import LoginPage from "../app/login/page";

describe("login destination", () => {
  it("opens Workspace by default after sign-in", async () => {
    const page = await LoginPage({ searchParams: Promise.resolve({}) });

    expect((page as ReactElement<{ returnTo: string }>).props.returnTo).toBe("/workspace");
  });

  it("preserves an explicitly requested internal destination", async () => {
    const page = await LoginPage({
      searchParams: Promise.resolve({ next: "/documentation" }),
    });

    expect((page as ReactElement<{ returnTo: string }>).props.returnTo).toBe("/documentation");
  });
});
