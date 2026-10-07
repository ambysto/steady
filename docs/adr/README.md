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
| [0010](0010-apple-app-structure.md) | Apple app structure, generated String Catalog and shared diagnosis test vectors | Accepted |
| [0011](0011-apple-measurement-history.md) | The Apple app keeps its measurement history in SQLite, in the Windows schema | Accepted |
| [0012](0012-user-sent-problem-reports.md) | Problem reports are written by the app, read in full and sent by the user | Accepted |
| [0013](0013-apple-release-from-a-mac.md) | Apple builds are archived and uploaded from a Mac, by script | Accepted |
| [0014](0014-merge-internet-drops.md) | Internet drops close together are one unstable episode | Accepted |
| [0015](0015-tweak-value-from-measurement.md) | A tweak whose value comes from a measurement (DNS) | Accepted |
| [0016](0016-measured-tweaks.md) | Measured tweaks: measure first, derive the value, keep the measurement with the backup | Accepted |
| [0017](0017-validate-backup-before-restore.md) | The elevated helper validates every backup entry against the tweak's own domain before restoring it | Accepted (amended by 0018) |
| [0018](0018-backups-in-hklm.md) | Backups live in HKLM, where only the elevated side can write (amends 0017) | Accepted |
| [0019](0019-per-machine-install.md) | Install for all users under Program Files; elevated code only runs from there | Accepted |
