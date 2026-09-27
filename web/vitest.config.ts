// Vitest config (docs/ui/DESIGN.md §6): jsdom, component and pure-helper tests under src/.
import { defineConfig, mergeConfig } from "vitest/config";
import viteConfig from "./vite.config";

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: "jsdom",
      include: ["src/**/*.test.{ts,tsx}"],
      setupFiles: ["src/test-setup.ts"],
    },
  }),
);
