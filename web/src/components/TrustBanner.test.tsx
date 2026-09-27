// TrustBanner: DESIGN §2 "Trust banner (Overview and Review; collapsible, never removed)": "Merges
// and overlaps are a ranked review queue, not decisions: … (κ 0.11, 160 labelled pairs) … 0 of 83
// labelled pairs below 0.5 were related; 9 of 10 related pairs had o ≥ 0.7. Splits passed (precision
// 0.67, AUC 0.82)"; figures from the run's calibration.json, else the newest same-kind run's
// (cited), else "not calibrated".
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TID } from "../lib/testids.ts";
import type { RunBundle, RunSummary } from "../lib/types.ts";
import { TrustBanner } from "./TrustBanner.tsx";

const RUN = { ref: "t3~20260926-150000", kind: "real" } as RunSummary;
const CALIBRATION = { rel_n: 160, rel_kappa: 0.1103, rel_pass: false, atomic_n: 40, atomic_precision: 0.6667, atomic_auc: 0.8211, atomic_pass: true };

function bundle(calibration: RunBundle["calibration"], from: string | null) {
  return { run: RUN, calibration, calibration_from: from };
}

const banner = () => screen.getByTestId(TID.trustBanner);
const toggle = () => screen.getByTestId(TID.trustToggle);

describe("TrustBanner (§2)", () => {
  it("says the queue is not decisions and gives the calibration figures", () => {
    render(<TrustBanner bundle={bundle(CALIBRATION, null)} />);
    const text = banner().textContent ?? "";
    expect(text).toContain("ranked review queue, not decisions");
    expect(text).toContain("κ 0.11");
    expect(text).toContain("160 labelled pairs");
    expect(text).toContain("0 of 83 labelled pairs below 0.5");
    expect(text).toContain("9 of 10 related pairs had o ≥ 0.7");
    expect(text).toContain("precision 0.67");
    expect(text).toContain("AUC 0.82");
  });

  it("cites another run's figures, and says not calibrated without figures while still warning", () => {
    render(<TrustBanner bundle={bundle(CALIBRATION, "t1~20260926-141229")} />);
    expect(banner().textContent).toContain("t1~20260926-141229");
    cleanup();
    render(<TrustBanner bundle={bundle(null, null)} />);
    expect(banner().textContent).toContain("not calibrated");
    expect(banner().textContent).toContain("not decisions");
  });

  it("collapses but is never removed: the warning stays and there is no close button", () => {
    render(<TrustBanner bundle={bundle(CALIBRATION, null)} />);
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(toggle());
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(banner().textContent).toContain("not decisions");
    expect(screen.queryByRole("button", { name: /close|dismiss|remove/i })).toBeNull();
  });

  it("says the o-band figures come from one backlog, with or without a calibration", () => {
    const line = "Figures measured on one 176-story backlog; see docs/results.md";
    render(<TrustBanner bundle={bundle(CALIBRATION, null)} />);
    expect(banner().textContent).toContain(line);
    cleanup();
    render(<TrustBanner bundle={bundle(null, null)} />);
    expect(banner().textContent).toContain(line);
  });

  it("starts collapsed on request and expands to the figures", () => {
    render(<TrustBanner bundle={bundle(CALIBRATION, null)} defaultCollapsed />);
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle());
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    expect(banner().textContent).toContain("κ 0.11");
  });
});
