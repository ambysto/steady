# ADR-0001: Python backend and a locally served web UI

- **Status:** Accepted — amended by [ADR-0002](0002-macos-style-ui-pywebview.md) (allows adding pywebview and small libraries)
- **Date:** 2026-10-03

## Context

The tool needs to: read/write Windows network configuration (PowerShell, netsh, powercfg, registry), monitor ping continuously, display realtime charts, and remain usable when the Internet is down. The development machine already has Python 3.11 and Node 26; PowerShell is only available as version 5.1.

## Decision

- **Python 3.11+** backend, preferring the standard library: `http.server`, `sqlite3`, `ctypes` (ICMP, Admin check), `winreg`, `subprocess`.
- **Plain HTML/CSS/JS** UI, loading no resources from a CDN (it must work while offline).
- The server binds only to `127.0.0.1` and is protected by a token (see [SECURITY.md](../SECURITY.md)).

## Consequences

- ✅ Nothing extra to install; easy to read and modify.
- ✅ The UI can be reused if it is later wrapped in a desktop window.
- ⚠️ A UI in a browser tab does not feel like a native application — being reconsidered in [OPEN-QUESTIONS.md](../OPEN-QUESTIONS.md) Q2/Q3; that decision may amend this ADR.
