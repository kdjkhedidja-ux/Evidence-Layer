import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RelationshipCanvas from "../app/relationships/relationship-canvas";

const records = [
  { id: "e1", source_label: "Policy A", content: "Alpha evidence body" },
  { id: "e2", source_label: "Policy B", content: "Beta evidence body" },
  { id: "e3", source_label: null, content: "Unlinked record" },
];

const relationships = [
  {
    id: "r1",
    evidence_id_a: "e1",
    evidence_id_b: "e2",
    relation_type: "supports" as const,
    rationale: "Policy A directly supports Policy B",
    provenance_note: "Reviewed against the 2026 filing",
    created_by: "verifier@example.test",
    updated_at: "2026-11-01T10:00:00Z",
  },
];

describe("relationship canvas", () => {
  it("renders one card per linked evidence and one edge per relationship", () => {
    render(<RelationshipCanvas records={records} relationships={relationships} />);
    expect(screen.getByRole("img", { name: /2 evidence records and 1 link/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Evidence Policy A/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Evidence Policy B/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Unlinked record/i })).not.toBeInTheDocument();
  });

  it("shows rationale and recorded basis when a link is selected", () => {
    render(<RelationshipCanvas records={records} relationships={relationships} />);
    fireEvent.click(screen.getByRole("button", { name: /Supports link: Policy A directly supports Policy B/i }));
    const details = screen.getByRole("region", { name: "Relationship details" });
    expect(within(details).getByText(/Policy A directly supports Policy B/)).toBeInTheDocument();
    expect(within(details).getByText(/Reviewed against the 2026 filing/)).toBeInTheDocument();
    expect(within(details).getByText(/From:/)).toBeInTheDocument();
    expect(within(details).getByText(/To:/)).toBeInTheDocument();
    expect(within(details).getByText(/Recorded by verifier@example.test/)).toBeInTheDocument();
  });

  it("summarizes the visible graph and keeps self-links finite", () => {
    const { container } = render(
      <RelationshipCanvas
        records={records}
        relationships={[{ ...relationships[0], evidence_id_b: "e1" }]}
      />,
    );
    expect(screen.getByRole("img", { name: /1 evidence records and 1 link/i })).toBeInTheDocument();
    expect(screen.getByText("1 record · 1 link")).toBeInTheDocument();
    expect(container.querySelector('path[d*="NaN"]')).toBeNull();
  });

  it("shows a node's links when a card is selected", () => {
    render(<RelationshipCanvas records={records} relationships={relationships} />);
    fireEvent.click(screen.getByRole("button", { name: /Evidence Policy A/i }));
    const details = screen.getByRole("region", { name: "Relationship details" });
    expect(within(details).getByText(/Policy B/)).toBeInTheDocument();
    expect(within(details).getByText(/directly supports/)).toBeInTheDocument();
    expect(within(details).getByRole("button", { name: "Open evidence" })).toBeInTheDocument();
  });

  it("zooms and resets the canvas", () => {
    const { container } = render(<RelationshipCanvas records={records} relationships={relationships} />);
    const group = () => container.querySelector("svg > g")?.getAttribute("transform") ?? "";
    expect(group()).toContain("scale(1)");
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(group()).toContain("scale(1.15)");
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(group()).toContain("scale(1)");
  });

  it("ignores links whose evidence is not loaded", () => {
    render(
      <RelationshipCanvas
        records={records}
        relationships={[{ ...relationships[0], evidence_id_b: "missing" }]}
      />,
    );
    expect(screen.getByRole("img", { name: /1 evidence records and 0 links/i })).toBeInTheDocument();
  });
});
