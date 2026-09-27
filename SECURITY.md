# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability** (a private security advisory). Do not open a public issue for a security problem. Include the version or commit, the steps to reproduce and what an attacker gains. We aim to acknowledge a report within a week.

## Supported versions

TesseLint is pre-1.0. Only the latest commit on `main` gets security fixes.

## Security model

TesseLint is a single-user tool that runs on your own machine. It has no accounts, no hosted service and no telemetry.

**The API key.** Your OpenRouter key lives in a gitignored `.env` file (copy `.env.example`) or in the environment. Only the CLI (`tesselint run`, `tesselint key`) reads it, and only to call OpenRouter. The key is never printed, logged, written to run outputs or sent to the browser; strings that look like a key (`sk-or-…`) are redacted from server logs and error messages.

**The local web UI (`tesselint serve`).**

- It binds to a loopback address only. A non-loopback `--host` is refused (exit 2), and requests whose `Host` header is not a loopback name are rejected, which blocks DNS rebinding.
- Each launch creates a random token and injects it into the page. Every state-changing request must carry it in the `X-TesseLint-Token` header, or it gets 403. The token is what blocks cross-site request forgery from other pages open in your browser. The server also refuses a request whose `Origin` or `Sec-Fetch-Site` header points to another site, but browsers don't always send those headers, so treat them as a second line only.
- Reads under `/api` are refused when `Sec-Fetch-Site` says another site made them, so a page elsewhere can't make the server start `tesselint key`.
- Every response carries this Content Security Policy: `default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`. Scripts load only from the server; inline styles are allowed. Responses also carry `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`, and API responses are `no-store`.
- `serve` never reads `.env` or the key. A re-run started from the UI spawns the `tesselint` CLI, which reads the key itself. If you export `OPENROUTER_API_KEY` in the shell that starts `serve`, the server process inherits it and passes its environment on to that CLI, but it never reads, logs or returns the key.
- Re-runs are limited to an allowlist of source files (`--source`, `TESSELINT_SOURCE` and the sources of existing runs) and to a spend cap (`--ui-max-usd`) that you confirm before each run.

**Your data.** Run outputs (`runs/`), the answer cache (`.cache/`) and UI decisions (`.tesselint/`) are local files that contain your backlog text, and they are gitignored. Story text is sent to OpenRouter and the Jev model provider when you run `tesselint run`; review their data policies before you send a confidential backlog.

## Out of scope

- Exposing `tesselint serve` beyond loopback (for example through a reverse proxy or port forwarding). It is not designed for that.
- Attacks that need local access to your user account, which can read `.env` directly.
