# ADR-0015: A tweak whose value comes from a measurement

- **Status:** Accepted
- **Date:** 2026-10-07

## Context

Every tweak in the catalog so far (ADR-0003) writes a value fixed in advance: a driver property, a registry DWORD, a powercfg index, a binding. `dns_fastest` is different. The best DNS servers depend on the network the PC is on today, so the value to write is only known after a benchmark (`app/dnsprobe.py`, diagnostics check #6). That breaks two assumptions of the framework:

- `read()` and `capture()` must have no side effects and are cheap, because the UI lists every tweak's state about once a minute and `list_states()` feeds diagnostics check #10. Sending DNS queries from there is neither.
- `capture()` runs before `apply()`, so it cannot know which servers `apply()` will choose, yet the backup it writes must be enough to undo whatever `apply()` does.

A second problem comes from outside the framework. The monitor reports "the DNS servers of this network changed" (`app/dnswatch.py`) as a warning with a toast, because that is what malware or a forgotten VPN looks like. A change the user asked the app to make must not raise that alarm.

## Decision

1. **The measurement happens in `apply()` only.** `read()` never measures. "On" is defined by the configuration, not by the measurement: the interface's IPv4 DNS is static, every server is in a fixed candidate list, they come from at least two providers, and each has DoH auto-upgrade on. A setup made by hand, with no help from the app, therefore reads as on, and enabling it is a no-op that creates no backup (ADR-0003, point 6).
2. **`capture()` records everything `apply()` might touch**, not what it will touch: the interface's GUID, whether its DNS was static or from DHCP and the servers, and the DoH flags of *every* candidate (present or not, auto-upgrade, fallback). `restore()` writes back only what differs and still looks like apply's work, removing a DoH entry that did not exist before and leaving an entry somebody else has changed. The comparison after a restore ignores the server list of a DHCP interface, because the lease may have changed in the meantime, and ignores such foreign entries.
   **The backup belongs to one interface.** The subject of a tweak can move: the uplink changes when a PC is docked or a VPN starts. So the framework gained two hooks, `recapture(sys, original)` (the read after a restore looks at the subject `original` names, found again by GUID, not at the current uplink) and `backup_conflict(sys, original)` (turning the tweak on while the backup is another subject's is refused, so a second original is never lost behind the first). `disable` no longer needs the machine to be readable when a backup exists: restoring from it works offline, with an unreadable uplink, or with a DoH list that cannot be read at that moment.
3. **The candidate list is closed and small**: the IPv4 addresses of the providers Windows ships a DoH template for (Cloudflare, Google, Quad9). The tweak never writes an address the user or a web page supplied. The winner is chosen by a pure function (`choose_dns_servers`): a server that failed any query is out, providers are ranked by their fastest server, and the result keeps two *different* providers in its first two places. Fewer than two working providers means nothing is changed.
4. **Everything that touches the network is injected** (`benchmark`, `captive`), so tests run the whole enable/disable cycle against `FakeSystem` and a scripted benchmark.
5. **Leave some networks alone.** The tweak reads as unsupported (so it is never suggested and `enable` refuses) while a VPN adapter is up, the PC is on a domain, the interface has a DNS suffix, it has a static IPv6 DNS (restoring with `-ResetServerAddresses` would erase it), or the connectivity probe is answered like a captive portal. The state is checked again inside `apply()`, because a VPN can come up between the read and the apply.
6. **dnswatch is told through events, not through shared memory.** The tweak manager already records `tweak_enabled` / `tweak_disabled` / `tweak_failed` with the tweak id in storage, which the monitor process shares with the elevated writer (ADR-0005). When the monitor is about to report a confirmed DNS change, it asks storage whether such an event for a DNS tweak happened in the last 5 minutes; if so it records `dns_observed` ("this app changed the DNS servers…") with level `info` and no toast. It is still asked only after the change has been seen twice, so the usual protection against a half-done renew is unchanged.
7. **Not a tweak that always works.** There is no default to restore without a backup (`has_default_restore = False`): after a DHCP lease the "original" is whatever the network says, and a static original cannot be guessed.
8. **Compare with the DNS in use, and say no before the UAC prompt** (added 2026-10-07, SIC-96). On the dev PC the tweak made DNS about 3× slower than the router's own DNS (EXP-021): the benchmark only ranked the public providers against each other. Now:
   - The DNS in use is benchmarked with the candidates, on two kinds of names: the common names of check #6 (where the router's cache makes it fast, which is what most browsing hits) and names no resolver can have cached (a fresh random label under eight large domains, so every server has to recurse).
   - Switching is worth it only when the DNS in use is broken (a server that lost a query or never answered, or none at all; the case check #6 points at this tweak for) or when the chosen public server is faster **on both kinds of names** by at least 20% and 10 ms (`check_public_dns_faster`, pure). The benchmark asks over plain DNS while the tweak sets DoH, which costs more per lookup, hence the margin.
   - The check runs twice: unelevated in the server before any UAC prompt (`Tweak.preflight`, a new framework hook; `TweakManager.preflight` returns the reason, or nothing when the check itself fails), and again inside `apply()` before the first write, because the network can change between the two. A refusal writes nothing.

## Consequences

- ✅ The framework (backup before apply, read back, roll back, one lock) is reused unchanged; only the declaration is new.
- ✅ Listing the tweaks stays cheap and harmless; the one request that can be slow (the captive-portal probe, a few seconds at most) only happens while the tweak is off and nothing cheaper already ruled it out.
- ✅ A change made by the app is recorded in the event log as such, so the history still explains why the DNS servers changed.
- ⚠️ Static DNS is a property of the interface, not of the network. Once on, the tweak reads the network again each time it is listed and shows a warning when it has become one it would not have been turned on for, but it does not turn itself off.
- ⚠️ The benchmark is a single run of five names per server: two providers a few milliseconds apart can swap places between runs. The tweak does not try to be exact; it avoids the clearly slow and the clearly broken.
- ⚠️ A DNS change by something else within 5 minutes of the app's own change is excused. The window is as short as two looks of the monitor (60 s each) allows.
- ⚠️ The preflight benchmark runs on every attempt to turn the tweak on (a few seconds of DNS queries, no Admin), and `apply()` runs it again. A switch that is worth it on paper can still be slower in use because of DoH; the effect report (ADR-0007) and EXP-021's method are how that is found.
- ⚠️ Other measured tweaks will want the same shape: decide in `apply()`, save the superset in `capture()`, define "on" structurally.
