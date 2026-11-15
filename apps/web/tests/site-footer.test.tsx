import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SiteFooter from "../components/site-footer";

describe("SiteFooter", () => {
  it("shows only legal links by default", () => {
    render(<SiteFooter />);
    const nav = screen.getByRole("navigation", { name: "Legal and product information" });
    expect(nav.querySelectorAll("a")).toHaveLength(3);
    expect(nav).toHaveTextContent("Privacy");
    expect(nav).toHaveTextContent("Terms");
    expect(nav).toHaveTextContent("Data Deletion");
    expect(nav).not.toHaveTextContent("Documentation");
  });

  it("adds Developer documentation only where requested", () => {
    render(<SiteFooter includeDeveloperDocs />);
    expect(screen.getByRole("link", { name: "Developer documentation" })).toHaveAttribute(
      "href",
      "/documentation",
    );
  });
});
