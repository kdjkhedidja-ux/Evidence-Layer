import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import CostPerformancePage, { metadata as costMetadata } from "../app/(public)/cost-performance/page";
import EvaluationBenchmarksPage, { metadata as evaluationMetadata } from "../app/(public)/evaluation/page";
import DocumentationPage, { metadata as documentationMetadata } from "../app/(public)/documentation/page";
import PrivacyPage, { metadata as privacyMetadata } from "../app/(public)/privacy/page";
import TermsPage, { metadata as termsMetadata } from "../app/(public)/terms/page";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: {
    href: string;
    children: ReactNode;
    [key: string]: unknown;
  }) => <a href={href} {...props}>{children}</a>,
}));

const routes = [
  { path: "/documentation", name: "Documentation", Page: DocumentationPage, metadata: documentationMetadata },
  { path: "/evaluation", name: "Evaluation · Benchmarks", Page: EvaluationBenchmarksPage, metadata: evaluationMetadata },
  { path: "/cost-performance", name: "Cost & Performance", Page: CostPerformancePage, metadata: costMetadata },
  { path: "/privacy", name: "Privacy Policy", Page: PrivacyPage, metadata: privacyMetadata },
  { path: "/terms", name: "Terms of Use", Page: TermsPage, metadata: termsMetadata },
] as const;
const styles = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");

describe("public information pages", () => {
  it.each(routes)("renders the $name route with metadata, landmarks, and working public navigation", ({ path, name, Page, metadata }) => {
    expect(metadata.title).toContain(name);
    expect(metadata.description).toBeTruthy();

    render(<Page />);

    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("main", { name: "" })).toHaveAttribute("id", "main-content");
    expect(screen.getByRole("heading", { level: 1, name })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();

    const headerNav = screen.getByRole("navigation", { name: "Main navigation" });
    const currentPageLink = within(headerNav).getByRole("link", { name, current: "page" });
    expect(currentPageLink).toHaveAttribute("href", path);

    for (const route of routes) {
      const matchingLinks = screen.getAllByRole("link", { name: route.name });
      expect(matchingLinks.some((link) => link.getAttribute("href") === route.path)).toBe(true);
    }
  });

  it("labels legal placeholders as publication blockers and states the current prototype boundary", () => {
    render(<PrivacyPage />);
    expect(screen.getByRole("heading", { name: "Complete these details before service launch" })).toBeInTheDocument();
    expect(screen.getByText(/controller identity and postal address/i)).toBeInTheDocument();
    expect(screen.getByText(/does not generate answers with OpenAI or DeepSeek/i)).toBeInTheDocument();
    expect(screen.getByText(/token counts, request duration, retrieval volume, estimated costs/i)).toBeInTheDocument();
  });

  it("documents that Evidence Layer selects evidence and the caller's model writes the answer", () => {
    render(<DocumentationPage />);
    expect(screen.getByRole("heading", { name: /selects and traces evidence; it does not write the final answer/i })).toBeInTheDocument();
    expect(screen.getByText(/caller.s chosen LLM consumes the selected context and writes the final answer/i)).toBeInTheDocument();
    const navigation = within(screen.getByRole("navigation", { name: "Main navigation" }));
    expect(navigation.getByRole("link", { name: "Evaluation · Benchmarks" })).toHaveAttribute("href", "/evaluation");
  });

  it("keeps Phase 6, Phase 7, and Phase 7B findings distinct and reports the required caveats", () => {
    render(<EvaluationBenchmarksPage />);
    expect(screen.getByRole("heading", { name: "Real-World Benchmark" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "End-to-End RAG Benchmark" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Fresh JEV in the end-to-end path" })).toBeInTheDocument();
    expect(screen.getByText("−24.75%")).toBeInTheDocument();
    expect(screen.getByText("−28.93%")).toBeInTheDocument();
    expect(screen.getByText(/DeepSeek interval includes zero/i)).toBeInTheDocument();
    expect(screen.getByText(/Coverage decreased by 0.0208/i)).toBeInTheDocument();
    expect(screen.getByText(/not been independently human-reviewed/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View the Phase 7 manifest" })).toHaveAttribute(
      "href",
      "/evaluation/phase7-manifest.html",
    );
    expect(screen.getByRole("link", { name: "Read the Phase 7 report" })).toHaveAttribute(
      "href",
      "/evaluation/phase7-report.html",
    );
    expect(screen.getByRole("link", { name: "Read the Phase 6 report" })).toHaveAttribute(
      "href",
      "/evaluation/phase6-report.html",
    );
    expect(screen.getByRole("link", { name: "Read the Phase 7B report" })).toHaveAttribute(
      "href",
      "/evaluation/phase7b-report.html",
    );
    expect(screen.getByRole("link", { name: "View the Phase 7B manifest" })).toHaveAttribute(
      "href",
      "/evaluation/phase7b-manifest.html",
    );
    for (const link of screen.getAllByRole("link")) {
      expect(link.getAttribute("href") ?? "").not.toMatch(/github\.com/i);
    }
  });

  it("explains the implemented account deletion scope without promising erasure", () => {
    render(<PrivacyPage />);
    expect(screen.getByRole("heading", { name: "9. Account deletion and data removal" })).toBeInTheDocument();
    expect(screen.getByText(/provider backups/i)).toBeInTheDocument();
    expect(screen.getByText(/Shared-workspace data/i)).toBeInTheDocument();

    render(<TermsPage />);
    expect(screen.getByText(/Account deletion requests\./i)).toBeInTheDocument();
    expect(screen.getByText(/not reversible in the current implementation/i)).toBeInTheDocument();
  });

  it("makes wide benchmark tables keyboard-scrollable and constrains public layouts on mobile", () => {
    render(<EvaluationBenchmarksPage />);
    const resultsRegion = screen.getByRole("region", { name: "Phase 7B live cost validation by provider" });
    expect(resultsRegion).toHaveAttribute("tabindex", "0");
    resultsRegion.focus();
    expect(document.activeElement).toBe(resultsRegion);
    expect(styles).toMatch(/\.public-table-scroll\s*\{[^}]*overflow-x:\s*auto;/);
    expect(styles).toMatch(/@media\s*\(max-width:\s*800px\)/);
    expect(styles).toMatch(/@media\s*\(max-width:\s*560px\)/);
  });

  it("renders the planning calculator and recommended operating metrics on Cost & Performance", () => {
    render(<CostPerformancePage />);
    expect(screen.getByRole("heading", { name: "Cost & Performance" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Token, API call, cost, and latency planner" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Metrics worth tracking in production" })).toBeInTheDocument();
    expect(screen.getByText("Cost per successful request")).toBeInTheDocument();
    expect(screen.getByText("End-to-end p50 / p95")).toBeInTheDocument();
    const navigation = within(screen.getByRole("navigation", { name: "Main navigation" }));
    expect(navigation.getByRole("link", { name: "Evaluation · Benchmarks" })).toHaveAttribute("href", "/evaluation");
  });

  it("shows legal and commercial placeholders without inventing the operator or governing law", () => {
    render(<TermsPage />);
    expect(screen.getByRole("heading", { name: "Complete these details before service launch" })).toBeInTheDocument();
    expect(screen.getByText(/Governing law and venue:/i)).toHaveTextContent("[insert after confirming the contracting entity");
    expect(screen.getByText(/No subscription, fees, quotas, refunds, service levels, or support terms have been approved/i)).toBeInTheDocument();
  });
});
