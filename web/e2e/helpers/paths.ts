// Filesystem locations the specs read (docs/ui/DESIGN.md §6; web/playwright.config.ts starts
// `serve --runs tests/fixtures/ui/runs --state web/e2e/.state --source tests/fixtures/ui/source.v1.json`
// from the repo root).
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const E2E_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const REPO = resolve(E2E_DIR, "..", "..");
export const FIXTURE_DIR = resolve(REPO, "tests/fixtures/ui");
export const MANIFEST_PATH = resolve(FIXTURE_DIR, "manifest.json");
export const SEED_PATH = resolve(FIXTURE_DIR, "decisions.seed.json");
export const SOURCE_PATH = resolve(FIXTURE_DIR, "source.v1.json");
export const EXPORT_SCHEMA = resolve(REPO, "schemas/decisions-export.v1.schema.json");

/** `--state web/e2e/.state` (DESIGN §3: decisions.json, .bak, exports/, jobs/, .lock). */
export const STATE_DIR = resolve(E2E_DIR, ".state");
export const DECISIONS_PATH = resolve(STATE_DIR, "decisions.json");
export const EXPORTS_DIR = resolve(STATE_DIR, "exports");
export const JOBS_DIR = resolve(STATE_DIR, "jobs");

/** Header carrying the CSRF token on every non-GET (DESIGN §4.1). */
export const TOKEN_HEADER = "X-TesseLint-Token";
