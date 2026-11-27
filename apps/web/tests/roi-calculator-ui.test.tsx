import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RoiCalculator from "../app/(public)/cost-performance/roi-calculator";

describe("ROI calculator card", () => {
  it("renders an estimate with the honesty disclaimers", () => {
    render(<RoiCalculator />);
    expect(screen.getByRole("heading", { name: "Token, API call, cost, and latency planner" })).toBeInTheDocument();
    expect(screen.getByText(/Estimates, not invoices/i)).toBeInTheDocument();
    expect(screen.getByText("DeepSeek 80.9%")).toBeInTheDocument();
    expect(screen.getByText("OpenAI 80.7%")).toBeInTheDocument();
    expect(screen.getByText("Anthropic 80.5%")).toBeInTheDocument();
    expect(screen.getByText(/Quality is not guaranteed/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Reduction scenario")).toHaveValue("conservative");
    expect(screen.getByText(/reduction 65.0%/i)).toBeInTheDocument();
    const region = screen.getByRole("status");
    expect(region.textContent).toMatch(/Tokens avoided/);
    expect(region.textContent).toMatch(/API calls \/ request/);
    expect(region.textContent).toMatch(/Latency p95/);
    expect(region.textContent).toMatch(/Cost \/ 1K requests/);
  });

  it("switches scenario and shows the JEV cost when selected", () => {
    render(<RoiCalculator />);
    fireEvent.change(screen.getByLabelText("Reduction scenario"), { target: { value: "median" } });
    expect(screen.getByText(/reduction 80.7%/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Evidence scorer"), { target: { value: "jev" } });
    const jevRow = screen.getByText("Estimated JEV").parentElement;
    expect(jevRow?.textContent).not.toContain("$0.00");
  });

  it("recomputes API calls and latency when the request count changes", () => {
    render(<RoiCalculator />);
    fireEvent.change(screen.getByLabelText("Requests per month"), { target: { value: "1000" } });
    const values = screen.getByRole("status").textContent ?? "";
    expect(values).toMatch(/1,000 \(990 successful\)/);
    expect(values).toMatch(/1,000 → 2,000/);
  });

  it("disables Evidence Layer results when the toggle is off", () => {
    render(<RoiCalculator />);
    fireEvent.click(screen.getByLabelText("Use Evidence Layer selection"));
    const values = screen.getAllByRole("status")[0].textContent ?? "";
    expect(values).toMatch(/Tokens avoided\s*0 \(0\.0%\)/);
  });
});
