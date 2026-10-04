# ADR-0004: Watchdog — pure decisions, persistent limits, self-disabling circuit breaker

- **Status:** Accepted
- **Date:** 2026-10-04

## Context

The watchdog deliberately interrupts the network (reconnecting, restarting the adapter). Getting one condition wrong turns it into a loop that causes its own outages. Real data shows: silent hangs with no Windows event at all (EXP-001, EXP-010); a steady 8–9% packet loss to the Internet while the router is fine (EXP-014, suspected ICMP rate limiting); sleep/wake creates gaps in the data; tweaks cause the adapter to restart. The monitor runs through Task Scheduler with standard (unelevated) rights.

## Decision

1. **Separate decision from execution.** `WatchdogPolicy` is a pure function that takes an observation (Wi‑Fi state and the time it changed, any open router outage, uplink, Admin rights, grace periods) and returns an action or a reason for skipping. All safety limits live here and are tested by replaying real data. Execution (`app/actions.py`) is the only module that interrupts the network.
2. **React only to PC↔router failures** (`wifi_down`, `router_unreachable`). Internet loss while the router is fine never leads to an action.
3. **Limits persist across restarts:** the per-hour cap is also counted from the log in the DB, so a process that crashes and restarts repeatedly cannot exceed the cap.
4. **The circuit breaker disables the watchdog** (written to `settings.json`) after `trip_after` consecutive ineffective actions; only the user can re-enable it.
5. **Step 1 never requires Admin** (reconnect; for a silent hang, disconnect and then reconnect), because the monitor runs unelevated by default. Restarting the adapter is step 2 and requires Admin.
6. **Grace periods** after the machine wakes (60s) and after a tweak changes the machine (120s); never take back a connection the user has just disconnected.
7. **`dry_run` mode** records decisions without carrying them out — a safe way to observe behavior on a real machine before enabling it.

## Consequences

- ✅ All safety logic is testable with a fake clock and replays of recorded outages.
- ✅ There is no way for the watchdog to run more than `max_per_hour` times per hour, even through a crash loop.
- ⚠️ Without Admin, the watchdog can only reconnect; if a silent hang requires an adapter restart, the task must be installed with `--highest`.
- ⚠️ A forced reconnect interrupts the network for a few seconds even if the silent hang happens to clear by itself at that exact moment — acceptable, since the 15s threshold has already been exceeded.
