// The `test` every fixture spec uses (docs/ui/DESIGN.md §6): the seed decisions are copied into the
// state dir before each test, and `manifest` gives the fixture expectations.
import { test as base, expect } from "@playwright/test";
import { loadManifest, type Manifest } from "./manifest.ts";
import { resetState } from "./state.ts";

export const test = base.extend<{ manifest: Manifest; seedReset: void }>({
  seedReset: [
    async ({}, use) => {
      resetState();
      await use();
    },
    { auto: true },
  ],
  manifest: async ({}, use) => {
    await use(loadManifest());
  },
});

export { expect };
