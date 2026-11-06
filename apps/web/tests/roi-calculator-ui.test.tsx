import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RoiCalculator from "../app/(public)/cost-performance/roi-calculator";

describe("ROI calculator card", () => {
  it("renders an estimate with the honesty disclaimers", () => {
    render(<RoiCalculator />);
    expect(screen.getByRole("heading", { name: "Token and cost calculator" })).toBeInTheDocument();
    expect(screen.getByText(/Estimates, not invoices/i)).toBeInTheDocument();
    expect(screen.getByText(/Quality is not guaranteed/i)).toBeInTheDocument();
    const region = screen.getByRole("status");
    expect(region.textContent).toMatch(/Tokens avoided/);
  });

  it("switches scenario and shows the JEV cost when selected", () => {
    render(<RoiCalculator />);
    fireEvent.change(screen.getByLabelText("Reduction scenario"), { target: { value: "conservative" } });
    expect(screen.getByText(/reduction 15.0%/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Evidence scorer"), { target: { value: "jev" } });
    const jevRow = screen.getByText("Estimated JEV").parentElement;
    expect(jevRow?.textContent).not.toContain("$0.00");
  });

  it("disables Evidence Layer results when the toggle is off", () => {
    render(<RoiCalculator />);
    fireEvent.click(screen.getByLabelText("Use Evidence Layer selection"));
    const values = screen.getAllByRole("status")[0].textContent ?? "";
    expect(values).toMatch(/Tokens avoided\s*0 \(0\.0%\)/);
  });
});
