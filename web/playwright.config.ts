// Playwright config (docs/ui/DESIGN.md §6): installed Chrome, one worker, the fixture server on 8799.
import { defineConfig } from "@playwright/test";

const PORT = 8799;

export default defineConfig({
  testDir: "e2e",
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env["CI"]),
  reporter: "list",
  use: {
    channel: "chrome",
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "uv run tesselint serve --runs tests/fixtures/ui/runs --state web/e2e/.state " +
      `--source tests/fixtures/ui/source.v1.json --port ${PORT} --no-open`,
    cwd: "..", // the paths above are relative to the repo root
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      // The fake CLI (tests/fakes/fake_cli.py) and a canary key: no real Jev call can happen.
      TESSELINT_UI_CLI: "uv run python tests/fakes/fake_cli.py",
      OPENROUTER_API_KEY: "sk-or-canary",
    },
  },
});
