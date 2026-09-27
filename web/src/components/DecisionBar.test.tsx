// DecisionBar: DESIGN §2 Review keys (a accept, r/R reject one/all, d defer, n note with Ctrl+Enter,
// u undo; inert in inputs except Esc and Ctrl+Enter), the aria-live line "Accepted merge US-14.5 into
// US-1.1 · u to undo", "a note alone keeps action: null", "a failed save rolls back and offers Retry",
// and §3 (accepting derives `superseded` for siblings: nothing is stored for them).
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TID } from "../lib/testids.ts";
import type { DecisionChange } from "../lib/types.ts";
import { DecisionBar, type DecisionBarProps } from "./DecisionBar.tsx";

const M1 = "merge:US-1.1>US-14.5";
const M2 = "merge:US-3.2>US-14.5";

function setup(overrides: Partial<DecisionBarProps> = {}) {
  const onSubmit = vi.fn<(changes: DecisionChange[]) => void>();
  const onUndo = vi.fn<() => void>();
  const onRetry = vi.fn<() => void>();
  render(
    <DecisionBar
      caseKey="mg:US-14.5"
      optionKeys={[M1, M2]}
      selected={M1}
      current={{}}
      saveState="idle"
      rev={41}
      onSubmit={onSubmit}
      onUndo={onUndo}
      onRetry={onRetry}
      {...overrides}
    />,
  );
  return { onSubmit, onUndo, onRetry };
}

const key = (k: string, init: Partial<KeyboardEventInit> = {}) =>
  fireEvent.keyDown(document.body, { key: k, shiftKey: k.length === 1 && k !== k.toLowerCase(), ...init });

describe("DecisionBar buttons (§2 Review)", () => {
  it("names every action; Accept submits one change for the selected option only and announces it", () => {
    const { onSubmit } = setup();
    for (const [id, name] of [
      [TID.acceptButton, /accept/i],
      [TID.rejectButton, /reject/i],
      [TID.rejectAllButton, /reject all/i],
      [TID.deferButton, /defer/i],
      [TID.noteButton, /note/i],
      [TID.undoButton, /undo/i],
    ] as const) {
      expect(screen.getByTestId(id)).toHaveProperty("tagName", "BUTTON");
      expect(screen.getByTestId(id).textContent + (screen.getByTestId(id).getAttribute("aria-label") ?? "")).toMatch(name);
    }
    fireEvent.click(screen.getByTestId(TID.acceptButton));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith([{ key: M1, action: "accept" }]);
    const live = screen.getByTestId(TID.liveRegion);
    expect(live.getAttribute("aria-live")).toBe("polite");
    expect(live.textContent).toBe("Accepted merge US-14.5 into US-1.1 · u to undo");
  });
});

describe("DecisionBar keys (§2 Review keys)", () => {
  it("a accepts the selected option", () => {
    const { onSubmit } = setup({ selected: M2 });
    key("a");
    expect(onSubmit).toHaveBeenCalledWith([{ key: M2, action: "accept" }]);
    expect(screen.getByTestId(TID.liveRegion).textContent).toBe("Accepted merge US-14.5 into US-3.2 · u to undo");
  });

  it("r rejects the selected option, R rejects every option of the case", () => {
    const { onSubmit } = setup();
    key("r");
    expect(onSubmit).toHaveBeenLastCalledWith([{ key: M1, action: "reject" }]);
    expect(screen.getByTestId(TID.liveRegion).textContent).toMatch(/^Rejected .* · u to undo$/);
    key("R");
    expect(onSubmit).toHaveBeenLastCalledWith([
      { key: M1, action: "reject" },
      { key: M2, action: "reject" },
    ]);
  });

  it("d defers and u undoes", () => {
    const { onSubmit, onUndo } = setup();
    key("d");
    expect(onSubmit).toHaveBeenCalledWith([{ key: M1, action: "defer" }]);
    key("u");
    expect(onUndo).toHaveBeenCalledTimes(1);
  });

  it("n opens a note; Ctrl+Enter saves the note alone, without an action", () => {
    const { onSubmit } = setup();
    key("n");
    const input = screen.getByTestId(TID.noteInput);
    expect(document.activeElement).toBe(input);
    fireEvent.change(input, { target: { value: "keep the admin part" } });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith([{ key: M1, note: "keep the admin part" }]);
  });

  it("keys typed in the note are inert; Esc closes the note without saving", () => {
    const { onSubmit, onUndo } = setup();
    fireEvent.click(screen.getByTestId(TID.noteButton));
    const input = screen.getByTestId(TID.noteInput);
    for (const k of ["a", "r", "d", "u"]) fireEvent.keyDown(input, { key: k });
    expect(onSubmit).not.toHaveBeenCalled();
    expect(onUndo).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByTestId(TID.noteInput)).toBeNull();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

describe("DecisionBar save state (§2 Retry, chrome `saved · rev N`)", () => {
  it("shows the saved rev", () => {
    setup({ saveState: "saved", rev: 41 });
    expect(screen.getByTestId(TID.decisionBar).textContent).toContain("rev 41");
  });

  it("offers Retry only after a failed save", () => {
    const { onRetry } = setup({ saveState: "failed" });
    fireEvent.click(screen.getByTestId(TID.retryButton));
    expect(onRetry).toHaveBeenCalledTimes(1);
    cleanup();
    setup({ saveState: "saving" });
    expect(screen.getByTestId(TID.decisionBar).textContent).toMatch(/saving/i);
    expect(screen.queryByTestId(TID.retryButton)).toBeNull();
  });
});
