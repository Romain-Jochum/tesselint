// Keyboard map (docs/ui/DESIGN.md §2: chrome keys, Review keys, Matrix keys). resolveKey is pure:
// it maps one key press, in a scope, to an action plus the next chord state. Keys are inert while
// focus is in an input, except Esc and Ctrl+Enter.

import { useEffect, useRef } from "react";
import type { N, S } from "./types.ts";
import type { RunTab } from "./router.ts";

/** Where the key was pressed: chrome keys work everywhere; review and matrix add their own. */
export type HotkeyScope = "global" | "review" | "matrix";

/** `g` + letter jumps to a tab (§2): o, r, m, c, u (Runs), e. */
export type TabTarget = RunTab | "runs";

export const TAB_KEYS: Readonly<Record<S, TabTarget>> = {
  o: "overview",
  r: "review",
  m: "matrix",
  c: "clusters",
  u: "runs",
  e: "export",
};

export type HotkeyAction =
  // chrome (§2): `/` search, `x` raw JSON, `?` help, `g`+key tabs, `t` theme
  | { type: "search" }
  | { type: "rawJson" }
  | { type: "help" }
  | { type: "tab"; tab: TabTarget }
  | { type: "theme" }
  // review (§2 Review keys)
  | { type: "accept" }
  | { type: "reject" } // r: this option
  | { type: "rejectAll" } // R: every option of the case
  | { type: "defer" }
  | { type: "note" }
  | { type: "undo" }
  | { type: "next" } // j
  | { type: "prev" } // k
  | { type: "pick"; option: N } // 1–9
  | { type: "openStory"; other: boolean } // o / O
  | { type: "conflict" } // c
  // matrix (§2 Story level): arrows move the focus cell, Enter opens the pair drawer
  | { type: "move"; dRow: N; dCol: N }
  | { type: "activate" }
  // anywhere, including inputs
  | { type: "escape" }
  | { type: "save" }; // Ctrl+Enter

/** The fields of a KeyboardEvent that matter, plus whether focus is in an editable element. */
export interface KeyInput {
  key: S;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  inInput: boolean;
}

/** Chord state: `g` waits for its second key. */
export interface HotkeyState {
  pendingG: boolean;
}

export const INITIAL_HOTKEY_STATE: HotkeyState = { pendingG: false };

const CHROME_KEYS: Readonly<Record<S, HotkeyAction>> = {
  "/": { type: "search" },
  x: { type: "rawJson" },
  "?": { type: "help" },
  t: { type: "theme" },
};

const REVIEW_KEYS: Readonly<Record<S, HotkeyAction>> = {
  a: { type: "accept" },
  r: { type: "reject" },
  R: { type: "rejectAll" },
  d: { type: "defer" },
  n: { type: "note" },
  u: { type: "undo" },
  j: { type: "next" },
  k: { type: "prev" },
  o: { type: "openStory", other: false },
  O: { type: "openStory", other: true },
  c: { type: "conflict" },
};

const MATRIX_KEYS: Readonly<Record<S, HotkeyAction>> = {
  ArrowUp: { type: "move", dRow: -1, dCol: 0 },
  ArrowDown: { type: "move", dRow: 1, dCol: 0 },
  ArrowLeft: { type: "move", dRow: 0, dCol: -1 },
  ArrowRight: { type: "move", dRow: 0, dCol: 1 },
  Enter: { type: "activate" },
};

const own = <T,>(map: Readonly<Record<S, T>>, key: S): T | null => (Object.hasOwn(map, key) ? map[key]! : null);

function scopeAction(key: S, scope: HotkeyScope): HotkeyAction | null {
  if (scope === "review") {
    if (/^[1-9]$/.test(key)) return { type: "pick", option: Number(key) };
    return own(REVIEW_KEYS, key);
  }
  return scope === "matrix" ? own(MATRIX_KEYS, key) : null;
}

/** Resolve one key press (§2); action is null when the key does nothing in this scope. */
export function resolveKey(
  input: KeyInput,
  scope: HotkeyScope,
  state: HotkeyState,
): { action: HotkeyAction | null; state: HotkeyState } {
  const idle = INITIAL_HOTKEY_STATE;
  const { key } = input;
  // Esc and Ctrl+Enter work everywhere, inputs included.
  if (key === "Escape") return { action: { type: "escape" }, state: idle };
  if (key === "Enter" && (input.ctrlKey || input.metaKey)) return { action: { type: "save" }, state: idle };
  // Inputs keep their keys; browser shortcuts (Cmd+R, Ctrl+A, Alt+D) are never ours.
  if (input.inInput || input.ctrlKey || input.metaKey || input.altKey) return { action: null, state: idle };
  if (state.pendingG) {
    const tab = own(TAB_KEYS, key);
    return { action: tab ? { type: "tab", tab } : null, state: idle };
  }
  if (key === "g") return { action: null, state: { pendingG: true } };
  return { action: own(CHROME_KEYS, key) ?? scopeAction(key, scope), state: idle };
}

/** Input types whose letters are not text entry (a focused checkbox still lets `g u` through). */
const NON_TEXT_INPUTS = new Set(["button", "checkbox", "color", "file", "image", "radio", "reset", "submit"]);

/** True for input, textarea, select and contenteditable targets (§2: keys inert in inputs). */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target instanceof HTMLInputElement) return !NON_TEXT_INPUTS.has(target.type);
  if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true;
  if (target instanceof HTMLElement && target.isContentEditable) return true;
  const host = target.closest("[contenteditable]");
  return host !== null && host.getAttribute("contenteditable") !== "false";
}

/** A pending `g` expires after this long (ms). */
export const CHORD_TIMEOUT_MS = 1500;

/**
 * Listen on window for keydown and call onAction for each resolved action in this scope.
 * Mount it once per page (chrome keys resolve in every scope, so two listeners would fire twice).
 */
export function useHotkeys(scope: HotkeyScope, onAction: (action: HotkeyAction) => void): void {
  const latest = useRef(onAction);
  useEffect(() => {
    latest.current = onAction;
  });
  useEffect(() => {
    let state = INITIAL_HOTKEY_STATE;
    let pendingAt = 0;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return;
      if (state.pendingG && Date.now() - pendingAt > CHORD_TIMEOUT_MS) state = INITIAL_HOTKEY_STATE;
      const input: KeyInput = {
        key: event.key,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        altKey: event.altKey,
        shiftKey: event.shiftKey,
        inInput: isEditableTarget(event.target),
      };
      const result = resolveKey(input, scope, state);
      if (result.state.pendingG && !state.pendingG) pendingAt = Date.now();
      state = result.state;
      if (!result.action) return;
      if (result.action.type !== "escape") event.preventDefault();
      latest.current(result.action);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [scope]);
}
