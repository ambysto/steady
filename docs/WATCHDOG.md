# Watchdog — self-healing

**Off** by default. Turn it on in the Optimize tab ("Tool features" group), or in `data/settings.json` (`"watchdog": {"enabled": true}`). Safety design: [ADR-0004](adr/0004-watchdog-safety.md).

The watchdog runs **inside the monitor process**, reads the monitor's live state every second, and runs actions in a separate thread so it does not block measurement.

## Outage detection

| Situation | Condition |
|---|---|
| `wifi_down` | The Wi‑Fi card is in the `disconnected` state for ≥ `threshold_s` (default 15s) |
| `router_unreachable` | Pinging the router fails continuously for ≥ `threshold_s`. Includes **silent hangs**: Wi‑Fi still reports `connected`, same BSSID, but no data flows (EXP-001 57s, EXP-010 ~641s) |
| *(not handled)* | Router OK but Internet lost ⇒ a router/ISP-side fault; restarting the card cannot help — only logged |

**Does not intervene** when:
- the current uplink is **not** the Wi‑Fi card: the default route goes through another physical card that is Up, such as Ethernet, a phone tethered over USB (media "Unspecified") or a USB 4G dongle. The PC still has a connection then, so resetting Wi‑Fi would only cut a path nobody is using. Virtual cards (VPN) still count as Wi‑Fi. A newly plugged-in card that is not in the list yet causes the list to be re-read immediately, once per card (bug found on 2026-10-04 while testing failover with an iPhone);
- the most recent Wi‑Fi disconnect was **by the user** (event 8003 "disconnected by the user") — it does not grab back a connection the user just dropped;
- within **60s after the machine wakes up** (`monitor_gap` event): the network is reconnecting by itself;
- within **120s after a tweak changed the machine**: a card restart caused by a tweak is not an outage;
- the circuit breaker is open (see below).

## Action ladder

| Step | `wifi_down` | `router_unreachable` |
|---|---|---|
| 1 | **Reconnect** the most recent profile: `netsh wlan connect` | **Forced reconnect**: `netsh wlan disconnect` then `connect` (forces re-association, no Admin needed) |
| 2 | **Restart the card**: `Restart-NetAdapter` (🛡 Admin) | **Restart the card** (🛡 Admin) |

The ladder is tried **once per outage**; if every step has been tried and the outage persists, it waits and does not repeat. Without Admin rights, step 2 is skipped and the reason is logged (the autostart task runs with normal rights by default; install with `--highest` so the watchdog can use step 2).

## Safety limits

| Setting (`settings.json` → `watchdog`) | Default | Purpose |
|---|---|---|
| `threshold_s` | 15 | How long an outage must last before intervening |
| `cooldown_s` | 120 | Minimum interval between two actions |
| `max_per_hour` | 6 | Cap on the number of actions in a sliding 60 minutes, **counting actions from before the process restarted** (read from the log) |
| `verify_s` | 60 | Wait this long after each action; outage not over ⇒ the action counts as **ineffective** |
| `trip_after` | 3 | Number of **consecutive** ineffective actions that opens the circuit breaker |
| `dry_run` | false | Only logs "what it would do", does not act — used to observe before turning it on for real |

### Choosing the threshold (`threshold_s`)

Replay of the 257 router outages recorded on 2026-10-03 (no action is assumed to have had any effect):

| Threshold | Outages reaching the threshold | Of which cleared on their own within 60s after intervention | Actions per day | Share of outage time handled |
|---|---|---|---|---|
| 15s | 68 | 49 (72%) | 31 | 83% |
| 30s | 46 | 32 (70%) | 24 | 74% |
| **45s** | **29** | **16 (55%)** | **20** | **64%** |
| 60s | 25 | 13 (52%) | 17 | 61% |

At 15–30s, most interventions land on outages that would have cleared on their own anyway, and each one causes a few seconds of network loss itself. **The development machine uses 45s, running `dry_run` since 2026-10-04 10:20** for comparison before turning it on for real. The default in code stays at 15s until trial-run data is available.

**Circuit breaker:** when it opens, the watchdog turns itself off (`enabled` = false, writes `tripped_at` to `settings.json`) and logs a `bad`-level event. It only runs again once the user turns it back on — even if the process restarts.

## Notifications (toast)

On by default (`settings.json` → `notify.enabled`, re-read every 10 seconds; the switch is also available at `/api/settings`). Toasts are shown for:

| When | Content |
|---|---|
| A network outage lasting ≥ 30s (`notify.outage_after_s`) | "Lost connection to the router" or "Internet is down", with how long it has been down |
| That outage ends | "Connection restored", with the duration — only if the outage was notified |
| `watchdog_action`, `watchdog_tripped`, `watchdog_error` | The log event's content |

No toast for: outages shorter than 30s, `dry_run` mode, skips, and "restored" after a gap while the machine was asleep (it does not know what happened, so it does not guess). Limits: the same title at most once per 30s, at most 6 toasts per 10 minutes. Toasts carry the name "Windows PowerShell" because the script has no AppUserModelID of its own.

## Log

| Event | Level | When |
|---|---|---|
| `watchdog_action` | warn | Performs (or "would perform" in `dry_run`) an action, with the reason |
| `watchdog_recovered` | info | The outage ended after an action, with the duration |
| `watchdog_ineffective` | warn | The action did not help after `verify_s` |
| `watchdog_skip` | info | Did not intervene despite an outage (no Admin, cap reached, disconnect by the user…) — logged only when the reason changes |
| `watchdog_tripped` | bad | Circuit breaker opened, the watchdog turned itself off |
| `watchdog_error` | bad | The action failed (command error) |
