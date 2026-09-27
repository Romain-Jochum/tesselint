// Theme: DESIGN §5 "the theme in localStorage (try/catch)", §2 `t` toggles it; applied as
// <html data-theme>.
import { describe, expect, it } from "vitest";
import { THEME_KEY, applyTheme, readStoredTheme, resolveTheme, storeTheme, toggleTheme } from "./theme.ts";

function throwingStorage(): Storage {
  const fail = () => {
    throw new DOMException("denied", "SecurityError");
  };
  return { length: 0, clear: fail, getItem: fail, key: fail, removeItem: fail, setItem: fail };
}

describe("theme storage (§5 try/catch)", () => {
  it("round-trips a stored theme, rejects junk, survives throwing storage; resolves, toggles and applies the theme", () => {
    const storage = window.localStorage;
    storeTheme("dark", storage);
    expect(storage.getItem(THEME_KEY)).toBe("dark");
    expect(readStoredTheme(storage)).toBe("dark");
    storage.setItem(THEME_KEY, "purple");
    expect(readStoredTheme(storage)).toBeNull();
    expect(readStoredTheme(throwingStorage())).toBeNull();
    expect(() => storeTheme("light", throwingStorage())).not.toThrow();
    expect(readStoredTheme(null)).toBeNull();
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme(null, true)).toBe("dark");
    expect(resolveTheme(null, false)).toBe("light");
    expect(toggleTheme("light")).toBe("dark");
    expect(toggleTheme("dark")).toBe("light");
    applyTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    const root = document.createElement("div");
    applyTheme("light", root);
    expect(root.getAttribute("data-theme")).toBe("light");
  });
});
