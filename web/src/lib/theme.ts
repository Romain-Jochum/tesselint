// Light/dark theme (docs/ui/DESIGN.md §5 "Routing and state": the theme lives in localStorage,
// wrapped in try/catch; §2 `t` toggles it). The theme is applied as <html data-theme="…">.

import type { S } from "./types.ts";

export type Theme = "light" | "dark";

/** localStorage key for the chosen theme. */
export const THEME_KEY = "tesselint.theme";

/** window.localStorage, or null when there is no window or storage access throws. */
function defaultStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

const isTheme = (value: unknown): value is Theme => value === "light" || value === "dark";

/** The stored theme, or null when unset, invalid or storage throws. */
export function readStoredTheme(storage?: Storage | null): Theme | null {
  const store = storage === undefined ? defaultStorage() : storage;
  try {
    const value = store?.getItem(THEME_KEY);
    return isTheme(value) ? value : null;
  } catch {
    return null;
  }
}

/** Store the theme; storage errors are swallowed. */
export function storeTheme(theme: Theme, storage?: Storage | null): void {
  const store = storage === undefined ? defaultStorage() : storage;
  try {
    store?.setItem(THEME_KEY, theme);
  } catch {
    // private mode or blocked storage: the theme simply is not remembered
  }
}

/** Whether the system asks for dark mode (false when matchMedia is unavailable). */
export function prefersDark(): boolean {
  try {
    return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches === true;
  } catch {
    return false;
  }
}

/** The stored theme, else the system preference. */
export function resolveTheme(stored: Theme | null, prefersDarkMode: boolean): Theme {
  return stored ?? (prefersDarkMode ? "dark" : "light");
}

/** The other theme. */
export function toggleTheme(theme: Theme): Theme {
  return theme === "dark" ? "light" : "dark";
}

/** Set data-theme on the root element (default document.documentElement). */
export function applyTheme(theme: Theme, root?: HTMLElement): void {
  const target = root ?? document.documentElement;
  target.setAttribute("data-theme", theme);
  target.style.colorScheme = theme;
}

/** Read a CSS token (e.g. "--cell-missing") as the painter needs it (§5 Heatmap). */
export function cssToken(name: S, root?: HTMLElement): S {
  return getComputedStyle(root ?? document.documentElement).getPropertyValue(name).trim();
}
