# ADR-0007: Measuring the impact of a change with before/after monitor data

- **Status:** Accepted
- **Date:** 2026-10-04

## Context

"Network booster" tools (IObit Internet Booster, TCP Optimizer…) change large numbers of system values without proving anything. Our monitor runs 24/7 and stores per-minute data, so it can show the user whether a change — enabling a tweak, or something done by hand such as rotating an antenna — actually made the network better. The risk is drawing wrong conclusions from too little data or from two time periods that are not comparable (daytime versus nighttime, periods when the machine was asleep).

## Decision

1. **Two windows of equal length** around the change time `t`: after = `[t, min(now, t + 24h, when the change was reverted))`, before = the same length immediately before `t`. With a full 24h on each side, the two windows cover the same hours of the day; if shorter, the result is labeled **preliminary**.
2. **Count only monitored time:** the number of minutes with ping data. Periods when the machine was asleep or the monitor was stopped are not counted as good or bad; frequencies are normalized to "per monitored day".
3. **Metrics:** number of outages (`router_down`, `internet_down`) per day, minutes of lost connectivity per day, % packet loss to the router, % of minutes with a collapsed link (Rx ≤ 30 Mbps together with packet loss ≥ 5%, as in check #13).
4. **Minimum thresholds:** ≥ 2 hours of monitoring are required on each side, otherwise the result is `collecting` (or `no_baseline` when there is no data from before the change). A metric is called better only when it drops by at least half **and** the before value is large enough to be meaningful (≥ 3 outages, ≥ 5 minutes of lost connectivity, ≥ 1% packet loss, ≥ 5% collapsed minutes); worse follows the symmetric rule. Everything else is "no clear difference".
5. **Overall verdict:** `better` / `worse` / `mixed` / `no_change`. Always accompanied by a reminder that this is a **measured correlation, not proof of causation** — the router, peak hours, or other devices could also be the cause.
6. The change time is taken from the log (`tweak_enabled` with `tweak_id`, `manual_step_done`); for a tweak enabled before that log existed, `captured_at` in `backup.json` is used (only when the backup was captured by the tool itself).

## Consequences

- ✅ Users see concrete evidence ("outages: 18 → 2 per day") instead of promises.
- ✅ No additional data or new tables are needed: the existing `minute_stats`, `wifi_stats`, `events` are used.
- ⚠️ Two changes close together (< 24h) have overlapping windows and their effects cannot be separated — the UI should state this clearly.
- ⚠️ If the network is already stable, most results will be "no clear difference" — that is correct, not a bug.
