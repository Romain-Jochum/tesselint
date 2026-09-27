// Keyboard map: DESIGN §2 Chrome keys (`/` search, `x` raw JSON, `?` help, `g` + o/r/m/c/u/e tabs,
// `t` theme), Review keys (a, r/R, d, n, u, j/k, 1–9, o/O, c; "keys are inert in inputs except Esc
// and Ctrl+Enter") and Matrix keys (arrows move the focus cell, Enter opens the pair drawer, Esc).
import { describe, expect, it } from "vitest";
import {
  INITIAL_HOTKEY_STATE,
  isEditableTarget,
  resolveKey,
  type HotkeyAction,
  type HotkeyScope,
  type KeyInput,
} from "./hotkeys.ts";

function press(key: string, scope: HotkeyScope, opts: Partial<KeyInput> = {}, state = INITIAL_HOTKEY_STATE) {
  const shiftKey = key === "?" || (key.length === 1 && key !== key.toLowerCase());
  return resolveKey({ key, ctrlKey: false, metaKey: false, altKey: false, shiftKey, inInput: false, ...opts }, scope, state);
}
const act = (key: string, scope: HotkeyScope, opts: Partial<KeyInput> = {}) => press(key, scope, opts).action;

describe("chrome keys (§2)", () => {
  it("/ x ? t work in every scope", () => {
    const table: [string, HotkeyAction][] = [
      ["/", { type: "search" }],
      ["x", { type: "rawJson" }],
      ["?", { type: "help" }],
      ["t", { type: "theme" }],
    ];
    for (const scope of ["global", "review", "matrix"] as const) {
      for (const [key, action] of table) expect(act(key, scope), `${key} in ${scope}`).toEqual(action);
    }
  });

  it("g then o/r/m/c/u/e opens a tab and ends the chord", () => {
    const tabs = { o: "overview", r: "review", m: "matrix", c: "clusters", u: "runs", e: "export" };
    for (const [key, tab] of Object.entries(tabs)) {
      const first = press("g", "global");
      expect(first.action).toBeNull();
      expect(first.state.pendingG).toBe(true);
      const second = press(key, "global", {}, first.state);
      expect(second.action, `g ${key}`).toEqual({ type: "tab", tab });
      expect(second.state.pendingG).toBe(false);
    }
  });

  it("the chord wins over review keys (g u is Runs, not undo); g + an unmapped key does nothing", () => {
    expect(press("u", "review", {}, press("g", "review").state).action).toEqual({ type: "tab", tab: "runs" });
    const stray = press("z", "global", {}, press("g", "global").state);
    expect(stray.action).toBeNull();
    expect(stray.state.pendingG).toBe(false);
  });
});

describe("review keys (§2 Review)", () => {
  it("map a r R d n u j k o O c", () => {
    const table: [string, HotkeyAction][] = [
      ["a", { type: "accept" }],
      ["r", { type: "reject" }],
      ["R", { type: "rejectAll" }],
      ["d", { type: "defer" }],
      ["n", { type: "note" }],
      ["u", { type: "undo" }],
      ["j", { type: "next" }],
      ["k", { type: "prev" }],
      ["o", { type: "openStory", other: false }],
      ["O", { type: "openStory", other: true }],
      ["c", { type: "conflict" }],
    ];
    for (const [key, action] of table) expect(act(key, "review"), key).toEqual(action);
  });

  it("1–9 pick an option; 0 does nothing", () => {
    for (let n = 1; n <= 9; n++) expect(act(String(n), "review")).toEqual({ type: "pick", option: n });
    expect(act("0", "review")).toBeNull();
  });

  it("review keys do nothing outside Review, and browser shortcuts (Cmd+R, Ctrl+A, Alt+D) are not decisions", () => {
    for (const key of ["a", "r", "R", "d", "j", "1"]) expect(act(key, "global"), key).toBeNull();
    expect(act("r", "review", { metaKey: true })).toBeNull();
    expect(act("a", "review", { ctrlKey: true })).toBeNull();
    expect(act("d", "review", { altKey: true })).toBeNull();
  });
});

describe("matrix keys (§2 Matrix story level)", () => {
  it("arrows move the focus cell, Enter opens the pair, Esc resets", () => {
    const table: [string, HotkeyAction][] = [
      ["ArrowUp", { type: "move", dRow: -1, dCol: 0 }],
      ["ArrowDown", { type: "move", dRow: 1, dCol: 0 }],
      ["ArrowLeft", { type: "move", dRow: 0, dCol: -1 }],
      ["ArrowRight", { type: "move", dRow: 0, dCol: 1 }],
      ["Enter", { type: "activate" }],
      ["Escape", { type: "escape" }],
    ];
    for (const [key, action] of table) expect(act(key, "matrix"), key).toEqual(action);
  });
});

describe("inputs (§2: keys are inert in inputs except Esc and Ctrl+Enter)", () => {
  it("letters, digits, `/` and a bare Enter do nothing while typing; Esc escapes and Ctrl+Enter saves", () => {
    for (const key of ["a", "r", "j", "1", "/", "t", "g", "x", "Enter"]) {
      expect(act(key, "review", { inInput: true }), key).toBeNull();
    }
    expect(act("Escape", "review", { inInput: true })).toEqual({ type: "escape" });
    expect(act("Enter", "review", { inInput: true, ctrlKey: true })).toEqual({ type: "save" });
  });

  it("isEditableTarget: input, textarea, select and contenteditable only", () => {
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    for (const el of [document.createElement("input"), document.createElement("textarea"), document.createElement("select"), editable]) {
      expect(isEditableTarget(el), el.tagName).toBe(true);
    }
    expect(isEditableTarget(document.createElement("button"))).toBe(false);
    expect(isEditableTarget(document.body)).toBe(false);
    expect(isEditableTarget(null)).toBe(false);
  });
});
