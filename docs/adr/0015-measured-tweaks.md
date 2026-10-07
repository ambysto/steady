# ADR-0015: Tweaks whose value comes from a measurement

- **Status:** Accepted
- **Date:** 2026-10-07

## Context

Every tweak in [ADR-0003](0003-tweak-framework.md) writes a fixed value (`Power Saving = Disabled`, `TcpTimedWaitDelay = 30`). Three speed changes made by hand on the development machine (see [TWEAKS.md](../TWEAKS.md), section 3b) do not have a fixed value:

- **Upload shaping**: a host-side upload limit just below the line's real upload rate keeps the queue in this PC instead of in the router, so latency under load stays low (measured by hand: maximum latency while uploading 1880 → 403 ms). The right limit depends on the line, and a limit copied from another line either does nothing or throttles the user.
- **MTU**: a PPPoE line carries 1492-byte packets; an interface left at 1500 loses large packets wherever Path MTU Discovery is blocked. The right value is whatever the path accepts, not "1492".
- **DNS**: the fastest public resolver depends on the network and the ISP's peering.

A wrong value here is worse than no tweak: a limit set for a 30 Mbps line cuts a 300 Mbps line by 90%. So the value must come from a measurement of the network in use, the measurement must be visible, and nothing may drift silently.

## Decision

1. **Calibrations.** A user-started measurement that produces a value a tweak can use is stored as a *calibration* in `data/calibration.json`: `{kind: {"value", "measured_at", "network", "detail"}}`, one entry per kind (`upload_mbps`, `path_mtu`, `dns_ranking`). `network` is the key from `dnswatch.network_key` (interface index, gateway, SSID) — local data only, never in the repo or a report. Written atomically, like `backup.json`. Sources: the bufferbloat test (check #14) writes `upload_mbps`; the Path MTU probe writes `path_mtu`; the DNS benchmark (check #6) writes `dns_ranking`.
2. **A measured tweak** derives its target from the calibration of its kind and otherwise follows ADR-0003 unchanged: backup before applying, read back, roll back on failure, restore from the backup. When no calibration exists for the **network in use**, or it is older than 30 days, the tweak is *not supported* with the reason "measure first" — it is never applied with a guessed value.
3. **Measuring while the tweak is on does not recalibrate.** With an upload limit active, the bufferbloat test measures the limit; with a lowered MTU, the probe cannot see above it. Such a measurement is shown to the user but does not replace the calibration (otherwise each re-measure would ratchet the value down). To recalibrate, the user turns the tweak off, measures, and turns it on again.
4. **Bounds are enforced in the tweak, not trusted from the file.** `calibration.json` is writable by the unelevated user, and the elevated helper ([ADR-0005](0005-unelevated-server-uac-writes.md)) reads it itself. So whatever the file says, the helper can only write: an upload limit of 85% of the measured rate, clamped to 2–1000 Mbps; an MTU of 1280–1500 and only lower than the current one; DNS servers from the built-in list of public resolvers that Windows has DoH templates for. Tampering with the file can at most choose among those safe values.
5. **Drift is reported, not corrected.** The tweak stays *on* while its setting exists. When the newest calibration (for this network) implies a value more than 15% away from the applied one, or the applied value was measured on another network, the state carries a note ("measured on another network / the line changed — turn off and on again to re-measure"). Nothing rewrites the machine automatically: the watchdog keeps only its recovery actions ([ADR-0004](0004-watchdog-safety.md)).
6. **The value shows its source.** The tweak's current value and note name the measurement it came from ("upload measured 42 Mbps on 2026-10-07 → limit 35.7 Mbps"), so the user can judge it and ADR-0007 can compare before and after.
7. **Scope of each setting.** The upload limit is a QoS policy owned by the app (a fixed name, `AmbystoSteadyUpload`), applied to all outgoing traffic; the app never touches other policies. MTU and DNS are set on the measured interface only. After a failover ([ADR-0008](0008-failover.md)) the upload limit still applies to the backup path; on a slower path it has no effect, on a faster one it caps it — the note says so.

## Consequences

- ✅ The same backup, verification and rollback code as every other tweak; uninstall restores measured tweaks like the rest.
- ✅ A value is never copied from one line to another, and the user sees where it came from.
- ⚠️ The upload limit also caps uploads inside the home network (copying to a NAS); the note says so and the risk is `medium`.
- ⚠️ Changing ISP plan or network needs an off–measure–on cycle; the drift note tells the user when.
- ⚠️ Download-side bufferbloat cannot be fixed from the PC: that needs SQM on the router (manual step `router_sqm`).
