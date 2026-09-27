// Vitest setup: unmount rendered trees between tests (globals are off, so RTL cannot self-register).
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
