import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import PageTitle from "../components/page-title";

describe("PageTitle", () => {
  it("keeps the first word in the heading color and accents the remainder", () => {
    render(<h1><PageTitle>Evidence workspace</PageTitle></h1>);
    expect(screen.getByRole("heading", { name: "Evidence workspace" })).toBeInTheDocument();
    expect(screen.getByText("workspace")).toHaveClass("page-title-accent");
  });

  it("supports multi-word remainders and single-word titles", () => {
    const { rerender } = render(<h1><PageTitle>Cost &amp; Performance</PageTitle></h1>);
    expect(screen.getByText("& Performance")).toHaveClass("page-title-accent");
    rerender(<h1><PageTitle>Documentation</PageTitle></h1>);
    expect(screen.getByRole("heading", { name: "Documentation" })).toBeInTheDocument();
    expect(document.querySelector(".page-title-accent")).toBeNull();
  });
});
