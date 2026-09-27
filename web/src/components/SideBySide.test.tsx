// SideBySide: DESIGN §2 Merge pane ("the dropped story beside the chosen container with bundled
// scores and shared phrases highlighted"; stale cases show a word diff), §5 Phrases ("they render as
// <mark>, never via innerHTML"; "hovering one lights its twin"), §2 chrome (every story ID links to
// its detail, title on hover).
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ATTR, TID } from "../lib/testids.ts";
import type { StoryRow } from "../lib/types.ts";
import { SideBySide } from "./SideBySide.tsx";

const RUN = "mini~20260102-090000";

function story(id: string, title: string, text: string): StoryRow {
  return { id, epic: "E1", title, tag: "", words: text.split(" ").length, text_hash: "a".repeat(64), text, atomic: 0.1, order: 0 };
}

const A = story("US-1.1", "Sales export", "As a manager I can export the monthly sales report to PDF");
const B = story("US-14.5", "Spreadsheet export", "Export the monthly sales report as a spreadsheet");

describe("SideBySide (§2 Merge pane, §5 Phrases)", () => {
  it("shows both stories with ID links to their detail pages, and their captions", () => {
    render(<SideBySide run={RUN} a={A} b={B} />);
    const [sideA, sideB] = [screen.getByTestId(TID.sideA), screen.getByTestId(TID.sideB)];
    expect(sideA.textContent).toContain("to PDF");
    expect(sideB.textContent).toContain("as a spreadsheet");
    const link = within(sideA).getByRole("link", { name: /US-1\.1/ });
    expect(link.getAttribute("href")).toBe(`/r/${RUN}/story/US-1.1`);
    expect(within(sideB).getByRole("link", { name: /US-14\.5/ }).getAttribute("href")).toBe(`/r/${RUN}/story/US-14.5`);
    cleanup();
    render(<SideBySide run={RUN} a={A} b={B} captions={["keep · bundled 0.12", "drop · bundled 0.40"]} />);
    expect(screen.getByTestId(TID.sideA).textContent).toContain("keep · bundled 0.12");
    expect(screen.getByTestId(TID.sideB).textContent).toContain("drop · bundled 0.40");
  });

  it("marks the shared phrase once per side with the same run index", () => {
    const { container } = render(<SideBySide run={RUN} a={A} b={B} />);
    const markA = within(screen.getByTestId(TID.sideA)).getAllByText((_, el) => el?.tagName === "MARK");
    const markB = within(screen.getByTestId(TID.sideB)).getAllByText((_, el) => el?.tagName === "MARK");
    expect(markA.map((m) => m.textContent)).toEqual(["export the monthly sales report"]);
    expect(markB.map((m) => m.textContent)).toEqual(["Export the monthly sales report"]);
    expect(markA[0]!.getAttribute(ATTR.run)).not.toBeNull();
    expect(markA[0]!.getAttribute(ATTR.run)).toBe(markB[0]!.getAttribute(ATTR.run));
    expect(container.querySelectorAll("mark")).toHaveLength(2);
  });

  it("hovering a phrase lights its twin", () => {
    const { container } = render(<SideBySide run={RUN} a={A} b={B} />);
    const [markA, markB] = Array.from(container.querySelectorAll("mark"));
    fireEvent.pointerEnter(markA!);
    fireEvent.mouseEnter(markA!);
    expect(markA!.getAttribute(ATTR.active)).toBe("true");
    expect(markB!.getAttribute(ATTR.active)).toBe("true");
    fireEvent.pointerLeave(markA!);
    fireEvent.mouseLeave(markA!);
    expect(markB!.getAttribute(ATTR.active)).not.toBe("true");
  });

  it("renders story text as text, never as HTML", () => {
    const hostile = story("US-9.1", "Hostile", 'Export the monthly sales report <img src=x onerror="alert(1)"> <b>now</b>');
    const { container } = render(<SideBySide run={RUN} a={A} b={hostile} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByTestId(TID.sideB).textContent).toContain('<img src=x onerror="alert(1)">');
  });

  it("shows a word diff of the stored text for a stale story (§2 stale cases)", () => {
    const old = "As a manager I can export the weekly sales report to PDF";
    render(<SideBySide run={RUN} a={A} b={B} oldTexts={{ [A.id]: old }} />);
    const diff = within(screen.getByTestId(TID.sideA)).getByTestId(TID.wordDiff);
    expect(Array.from(diff.querySelectorAll("del")).map((d) => d.textContent?.trim())).toEqual(["weekly"]);
    expect(Array.from(diff.querySelectorAll("ins")).map((d) => d.textContent?.trim())).toEqual(["monthly"]);
    expect(within(screen.getByTestId(TID.sideB)).queryByTestId(TID.wordDiff)).toBeNull();
  });
});
