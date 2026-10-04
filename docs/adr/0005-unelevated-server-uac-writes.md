# ADR-0005: Server runs unelevated; Admin operations run separately through UAC

- **Status:** Accepted — supersedes the "backend runs as Administrator" assumption in the first version of SECURITY.md
- **Date:** 2026-10-04

## Context

The initial design had the whole backend running as Admin. In practice:
- The monitor (where the live data is) runs 24/7 through Task Scheduler with **standard rights** ([ADR-0004](0004-watchdog-safety.md), SIC-11).
- The HTTP server is the part most exposed to untrusted data (any web page in the browser can send requests to `127.0.0.1`).
- Operations that require Admin are rare: enabling/disabling tweaks, restarting the adapter.

## Decision

1. **The server runs inside the monitor process, unelevated.** It reads live data directly from the monitor's memory; it never elevates itself.
2. **Operations that require Admin** run in a separate child process (`python -m app.elevated`) launched with `ShellExecuteEx` using the `runas` verb ⇒ Windows shows **a UAC prompt every time**. The child process accepts only a **fixed list of commands** (enable/disable a tweak that is in the catalog, restart an adapter by a valid name), re-validates the parameters itself (it does not trust the parent process), and writes results only to the user's own directory.
3. If the process itself already has Admin rights (the user installed the task with `--highest`), the operation is performed directly, without UAC — all of the server's protection layers remain in place.
4. **The watchdog never requests UAC** (nobody is sitting at the machine to click it): if rights are missing, it skips the adapter restart step as before.
5. Server protections (details in [SECURITY.md](../SECURITY.md)): bind only to `127.0.0.1`; check `Host` (against DNS rebinding); a random token on every start, embedded in the UI page, with the `X-Token` header required for every `/api/*` (constant-time comparison); write requests are rejected if the `Origin` is unknown or `Sec-Fetch-Site: cross-site`; no CORS headers are returned; security headers (CSP, anti-framing, `nosniff`); limits on body size and socket timeouts.

## Consequences

- ✅ A bug in the server does not become an Admin privilege escalation; an attacker who gets past the server can at most trigger a UAC prompt that the user has to click themselves.
- ✅ Every system change has explicit consent from Windows, in line with the "write only when the user agrees" principle.
- ⚠️ Every tweak enable/disable shows a UAC prompt. Acceptable because this operation is rare.
- ⚠️ A UAC prompt launched from a background process may only flash on the taskbar instead of appearing in the foreground right away.
