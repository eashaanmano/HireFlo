import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Funnel } from "./charts";
import { Reasons } from "./Reasons";

describe("Funnel", () => {
  it("scales each stage against the first so drop-offs are visible", () => {
    render(
      <Funnel
        stages={[
          { stage: "Applied", count: 10 },
          { stage: "Shortlisted", count: 5 },
          { stage: "Offer", count: 0 },
        ]}
      />,
    );
    expect(screen.getByTestId("bar-Applied")).toHaveStyle({ width: "100%" });
    expect(screen.getByTestId("bar-Shortlisted")).toHaveStyle({ width: "50%" });
    expect(screen.getByTestId("bar-Offer")).toHaveStyle({ width: "0%" });
  });

  it("shows the hiring target when given", () => {
    render(<Funnel stages={[{ stage: "Applied", count: 2 }]} target={4} />);
    expect(screen.getByText("Target: 4 hires")).toBeInTheDocument();
  });
});

describe("Reasons", () => {
  it("lists failed checks with their policy code", () => {
    render(
      <Reasons
        reasons={[
          { code: "rule:1", ok: true, message: "CGPA 8.2 meets the required 7", field: "cgpa", fixable: false },
          { code: "P1", ok: false, message: "Only higher tiers after an offer", field: null, fixable: false },
        ]}
        onlyFailed
      />,
    );
    expect(screen.queryByText(/meets the required/)).not.toBeInTheDocument();
    expect(screen.getByText("Policy P1")).toBeInTheDocument();
  });
});
