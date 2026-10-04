# Architecture Decision Records

Each architecture decision is a file `NNNN-short-title.md` with the sections: **Status** (Proposed / Accepted / Superseded), **Context**, **Decision**, **Consequences**.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-python-stdlib-web-ui.md) | Python backend and a locally served web UI | Accepted (amended by 0002) |
| [0002](0002-macos-style-ui-pywebview.md) | macOS-style UI in a pywebview window | Accepted |
| [0003](0003-tweak-framework.md) | Tweak framework: back up first, verify afterwards, roll back on failure | Accepted |
| [0004](0004-watchdog-safety.md) | Watchdog: pure decisions, persistent limits, self-disabling circuit breaker | Accepted |
| [0005](0005-unelevated-server-uac-writes.md) | Server runs unelevated; Admin operations run separately through UAC | Accepted |
| [0006](0006-i18n.md) | Internationalization: JSON catalogs, English by default, store messages rather than text | Accepted |
| [0007](0007-measured-impact.md) | Measuring the impact of a change with before/after monitor data | Accepted |
| [0008](0008-failover.md) | Failover to a backup path using interface metrics | Accepted |
| [0009](0009-native-apple-and-android-apps.md) | Native apps: SwiftUI for Apple platforms, Kotlin for Android, sharing data rather than code | Accepted |
