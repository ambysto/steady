# ADR-0008: Failover to a backup network path using interface metrics

- **Status:** Accepted
- **Date:** 2026-10-04
- **Related:** [ADR-0003](0003-tweak-framework.md) (backup/restore), [ADR-0004](0004-watchdog-safety.md) (safety limits), [ADR-0005](0005-unelevated-server-uac-writes.md) (writes requiring Admin)

## Context

The clearest stability improvement comes from having **≥ 2 paths to the Internet** (Wi‑Fi + LAN, USB 4G, phone tethering…). Windows automatically switches to another path when an adapter **loses its physical connection**, but does nothing when the adapter is still "Connected" while the Internet behind the router is dead (ISP failure, hung router) — exactly the most common case. Speedify solves this with an intermediary server (which keeps TCP sessions alive); we choose a lightweight approach that needs no server: change the route priority order.

## Decision

1. **Path** = a physical adapter (not a VPN/virtual adapter) that is Up and has an IPv4 default route through a gateway. The path list is re-read every 60s; the primary path = the path Windows is currently using (`GetBestRoute`).
2. **Probe each path separately**: a TCP connection to `1.1.1.1:443` / `8.8.8.8:443` with the socket **bound to that path's IP** (Windows uses the strong host model when sending, so packets leave through the correct adapter) every 5s. A single success means the path is still usable.
3. **Switch (failover)** when: the primary path has been continuously failing for ≥ `threshold_s` (20s) **and** there is a backup path that has been continuously healthy for ≥ 10s. Action: set the backup path's **InterfaceMetric** low enough for it to win (`Set-NetIPInterface`), without touching the primary path.
4. **Switch back (failback)** when the primary path has been continuously healthy for ≥ `failback_s` (120s): restore the backup path's original metric. Disabling the feature, the monitor finding a still-changed metric on startup (crash), or uninstalling ⇒ also restores it.
5. **Back up before writing**: the original metric (`AutomaticMetric`, `InterfaceMetric`) is saved to `backup.json` under the key `failover:<ifIndex>` *before* changing it; read back to verify; delete the backup only after restoring (same as ADR-0003).
6. **Safety limits** (same as ADR-0004, a pure, testable policy): ≥ 60s spacing between two switches, at most 6 per hour, switching back and forth ≥ 3 times within 30 minutes ⇒ **circuit breaker**: disable the feature automatically, restore the metric, notify the user. There is a `dry_run` mode that only records "what it would do". **Disabled by default.**
7. **Admin rights**: changing the metric requires Admin. If the monitor runs with Admin rights (task `--highest`), it switches automatically; otherwise it only **notifies** (an event + a toast "a backup path is available") and the UI has a "Switch now" button — at that point UAC asks the user (ADR-0005). Never show a UAC prompt when the user has not actively clicked.
8. **Who owns a switch** (`source` in the backup): the user clicks "Switch now" while failover is **disabled** ⇒ `manual`: kept until the user switches back, reverted automatically only if that path fails. Failover switched automatically, or the user clicked while failover is **enabled** ⇒ `failover`: switches back automatically as in item 4.
9. **Switch/restore failures** (adapter unplugged, blocked by policy…): notify **once**, retry after 60s, 5 minutes, then 15 minutes, not on every tick. The backup is kept until the restore succeeds; Windows stores the metric per interface, so plugging it back in allows it to be restored, and the UI still has a "Switch back" button for an unplugged path.
10. **Uncertain probe ≠ failure**: binding to an old IP fails (DHCP assigned a new IP) ⇒ "unknown", and the path list is re-read immediately; machine sleep (two ticks > 30s apart) ⇒ forget the "healthy/failing for how long" durations so as not to switch by mistake after waking.
11. `backup.json` is written by the monitor, the Admin process and the uninstaller alike: every write holds an inter-process lock (`config.backup_lock`), re-reads the file right inside the lock and changes only its own entry.

## Consequences

- ✅ No server needed, no extra libraries; the primary path is left intact.
- ⚠️ TCP sessions open on the old path will drop when switching (applications reconnect by themselves); unlike Speedify.
- ⚠️ A metered backup path (4G) may use up data — the UI clearly shows which path is in use.
- ⚠️ On a machine with only one path, the feature does nothing (it reports "no backup path yet").
- 📌 Not to be applied on the development machine unless the user has agreed (CLAUDE.md).
