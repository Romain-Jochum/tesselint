// JSON API client (docs/ui/DESIGN.md §7.2; token and guard §4.1; Re-run spend rules and exit codes §2).
// POSTs send X-TesseLint-Token (read from the tesselint-token meta tag) and a JSON body. Errors
// are {"error": {"code", "message"}} and surface as ApiError. The browser never sends an argv or a path.

import type {
  ApiErrorBody,
  DecisionsRequest,
  DecisionsResponse,
  ErrorCode,
  Estimate,
  EstimateRequest,
  ExportWritten,
  Health,
  Job,
  JobStartRequest,
  KeyStatus,
  MatrixPacked,
  N,
  PairDetail,
  ProposalsResponse,
  RunBundle,
  RunDiff,
  RunsResponse,
  S,
  ThOverrides,
} from "./types.ts";

/** A non-2xx API reply (§7.2): HTTP status plus the body's error code and message. */
export class ApiError extends Error {
  readonly status: N;
  readonly code: ErrorCode | S;

  constructor(status: N, code: ErrorCode | S, message: S) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** Name of the meta tag `serve` fills with the CSRF token (§4.1). */
export const TOKEN_META = "tesselint-token";
/** Header carrying the token on every non-GET (§4.1). */
export const TOKEN_HEADER = "X-TesseLint-Token";

/** Read the token from `<meta name="tesselint-token">` (§4.1); "" when absent. */
export function readToken(doc?: Document): S {
  const root = doc ?? (typeof document === "undefined" ? undefined : document);
  return root?.querySelector(`meta[name="${TOKEN_META}"]`)?.getAttribute("content") ?? "";
}

/** Build an ApiError from a non-2xx reply, reading {"error": {"code", "message"}} when present. */
async function toApiError(response: Response): Promise<ApiError> {
  let code: S = `http_${response.status}`;
  let message: S = response.statusText || `HTTP ${response.status}`;
  try {
    const body = (await response.json()) as Partial<ApiErrorBody> | null;
    if (typeof body?.error?.code === "string") code = body.error.code;
    if (typeof body?.error?.message === "string") message = body.error.message;
  } catch {
    // not JSON: keep the HTTP status as the code
  }
  return new ApiError(response.status, code, message);
}

/** fetch + JSON with typed errors; a network failure is ApiError(0, "network"). */
async function request<T>(path: S, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    throw new ApiError(0, "network", error instanceof Error ? error.message : String(error));
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

/** GET a JSON endpoint; throws ApiError on a non-2xx reply (§7.2). */
export function getJson<T>(path: S): Promise<T> {
  return request<T>(path, { headers: { Accept: "application/json" } });
}

/** POST a JSON body with the token header; throws ApiError on a non-2xx reply (§7.2, §4.1). */
export function postJson<T>(path: S, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json", [TOKEN_HEADER]: readToken() },
    body: JSON.stringify(body ?? {}),
  });
}

/** `?a=1&b=2` from the defined values, or "" (booleans as 1/0). */
function qs(params: Record<S, S | N | boolean | undefined>): S {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value !== undefined) search.set(name, typeof value === "boolean" ? (value ? "1" : "0") : String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const thParams = (th?: ThOverrides) => ({ merge: th?.merge, cluster: th?.cluster, split: th?.split });
const enc = encodeURIComponent;

// ---- GET (§7.2) ----

/** GET /api/health */
export function getHealth(): Promise<Health> {
  return getJson("/api/health");
}

/** GET /api/runs (with include=dry when includeDry) */
export function getRuns(options?: { includeDry?: boolean }): Promise<RunsResponse> {
  return getJson(`/api/runs${qs({ include: options?.includeDry ? "dry" : undefined })}`);
}

/** GET /api/runs/{ref} */
export function getRun(ref: S): Promise<RunBundle> {
  return getJson(`/api/runs/${enc(ref)}`);
}

/** GET /api/runs/{ref}/matrix */
export function getMatrix(ref: S): Promise<MatrixPacked> {
  return getJson(`/api/runs/${enc(ref)}/matrix`);
}

/** GET /api/runs/{ref}/proposals?merge&cluster&split */
export function getProposals(ref: S, th?: ThOverrides): Promise<ProposalsResponse> {
  return getJson(`/api/runs/${enc(ref)}/proposals${qs(thParams(th))}`);
}

/** GET /api/runs/{ref}/pairs/{a}/{b} */
export function getPair(ref: S, a: S, b: S): Promise<PairDetail> {
  return getJson(`/api/runs/${enc(ref)}/pairs/${enc(a)}/${enc(b)}`);
}

/** GET /api/diff?a&b */
export function getDiff(a: S, b: S): Promise<RunDiff> {
  return getJson(`/api/diff${qs({ a, b })}`);
}

/** GET /api/decisions?run&merge&cluster&split */
export function getDecisions(run: S, th?: ThOverrides): Promise<DecisionsResponse> {
  return getJson(`/api/decisions${qs({ run, ...thParams(th) })}`);
}

/** URL of GET /api/export?run&format&include_stale (a file download). */
export function exportUrl(run: S, format: "md" | "json", includeStale: boolean): S {
  return `/api/export${qs({ run, format, include_stale: includeStale })}`;
}

/** GET /api/key */
export function getKey(): Promise<KeyStatus> {
  return getJson("/api/key");
}

/** GET /api/jobs: the active (or most recent) job, null when there is none. */
export function getActiveJob(): Promise<Job | null> {
  return getJson("/api/jobs");
}

/** GET /api/jobs/{id} (polled each second while running, §5). */
export function getJob(id: S): Promise<Job> {
  return getJson(`/api/jobs/${enc(id)}`);
}

// ---- POST (§7.2) ----

/** POST /api/decisions: keys only; the server fills hashes, texts and titles. */
export function postDecisions(request: DecisionsRequest): Promise<DecisionsResponse> {
  return postJson("/api/decisions", request);
}

/** POST /api/decisions/undo (409 nothing_to_undo). */
export function undoDecisions(run: S): Promise<DecisionsResponse> {
  return postJson("/api/decisions/undo", { run });
}

/** POST /api/exports -> 201 {md_path, json_path} */
export function writeExports(run: S, includeStale: boolean): Promise<ExportWritten> {
  return postJson("/api/exports", { run, include_stale: includeStale });
}

/** POST /api/estimates -> 201 Estimate */
export function postEstimate(request: EstimateRequest): Promise<Estimate> {
  return postJson("/api/estimates", request);
}

/** POST /api/jobs -> 202 Job (409 job_running or estimate_stale, 422 cap_invalid). */
export function startJob(request: JobStartRequest): Promise<Job> {
  return postJson("/api/jobs", request);
}

/** POST /api/jobs/{id}/cancel -> 202 Job */
export function cancelJob(id: S): Promise<Job> {
  return postJson(`/api/jobs/${enc(id)}/cancel`, {});
}

// ---- Re-run spend rules and job messages (§2 Re-run) ----

/** Exit-code messages (§2 Re-run). Exit 4 shows the CLI's own error line instead. */
export const EXIT_MESSAGES: Readonly<Record<N, S>> = {
  3: "Stopped at the cap; resume pays only missing answers",
  5: "Jev refused the key or credits",
  6: "Too many failed calls",
  130: "Cancelled; partial outputs kept",
  1: "Unexpected; run it with --debug",
};

/** The message for a finished job: EXIT_MESSAGES, or the CLI's error line for exit 4 (§2). */
export function exitMessage(job: Pick<Job, "exit_code" | "message">): S | null {
  const code = job.exit_code;
  if (code === null || code === 0) return null;
  if (code === 4) return job.message;
  return EXIT_MESSAGES[code] ?? job.message ?? `Exited with code ${code}`;
}

/** Round up to the cent, ignoring binary noise below 1e-6 cent (1.3 × 0.9 is 1.17, not 1.18). */
function ceilCent(usd: N): N {
  return Math.ceil(Number((usd * 100).toFixed(6))) / 100;
}

/** Default cap: min(ceil_cent(1.3 × estimate), remaining, ui_max_usd) (§2 Re-run step 3). */
export function defaultCap(estimateUsd: N, remainingUsd: N | null, uiMaxUsd: N): N {
  return Math.max(0, Math.min(ceilCent(1.3 * estimateUsd), remainingUsd ?? Infinity, uiMaxUsd));
}

/** Start is enabled only when ticked and estimate ≤ cap ≤ min(remaining, ui_max_usd) (§2, §4.4). */
export function canStart(input: {
  estimateUsd: N;
  capUsd: N;
  remainingUsd: N | null;
  uiMaxUsd: N;
  accepted: boolean;
}): boolean {
  const { estimateUsd, capUsd, remainingUsd, uiMaxUsd, accepted } = input;
  if (!accepted || !Number.isFinite(capUsd)) return false;
  return estimateUsd <= capUsd && capUsd <= Math.min(remainingUsd ?? Infinity, uiMaxUsd);
}
