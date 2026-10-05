# ADR-0014: Internet drops close together are one unstable episode

- **Status:** Accepted
- **Date:** 2026-10-05
- **Related:** [ARCHITECTURE.md](../ARCHITECTURE.md), [WATCHDOG.md](../WATCHDOG.md), SIC-69

## Context

On 2026-10-05 the connection was unstable for about two minutes: the router answered the whole time, while Internet pings and probes failed in bursts. The monitor wrote two `internet_down` events, 30 s and 6 s, with 37 s between them. The cause is how an outage closes: it ends at the first tick where any ping answers or the latest TCP/HTTP probe round succeeded, so one good probe round in the middle of a bad stretch closes the outage. History and the "back online" toast then describe an unstable episode as separate short outages that do not add up to what the user felt.

ICMP loss alone is not the measure here (a provider may rate-limit ICMP while traffic is fine, EXP-014); the episode boundaries are still the outage start and end the tracker already computes from pings plus probes.

## Decision

1. `OutageTracker` holds a closed Internet outage for `MERGE_WINDOW_S` (60 s). If another Internet outage is declared within that time, the two belong to one episode.
2. One `internet_down` event is written per episode: `ts` is the last recovery, `duration` runs from the first drop to the last recovery, and the message says how many drops there were ("…; unstable: 3 drops"). The router-overlap classification is kept if any drop had it.
3. `active()` is unchanged: it always shows what is down right now, so the tray state, toasts and the watchdog behave as before. Only the stored event is merged; it appears up to 60 s after the last recovery.
4. A held episode is reported at once when the monitor is interrupted (sleep, hang: the open drop is cut at the last tick, as before) and when the monitor stops.
5. `router_down` is not merged in this change.

## Consequences

- ✅ One unstable episode is one history entry; the 2026-10-05 sequence becomes a single event of 73 s with 2 drops.
- ✅ No change to the watchdog or the toast logic; ICMP-only loss still never opens an outage.
- ⚠️ The event reaches the database up to 60 s late, and `duration` includes the short good stretches between drops. `impact.py` counts such an episode as one outage whose seconds include them.
- ⚠️ Flapping `router_down` events are still separate.
