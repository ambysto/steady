# Optimization catalog (Tweaks)

Each tweak is a toggle with 4 operations: **read** (read the state) · **capture** (save the original value) · **apply** (turn on) · **restore** (turn off, return to the original value).

Legend: 🔌 = network drops for a few seconds when applied · 🔁 = requires a restart · 🛡 = requires Admin rights

Common mechanism (back up first, verify, undo, restore when there is no backup): see [ADR-0003](adr/0003-tweak-framework.md). The powercfg group in particular **cannot** be turned off without a backup, because there is no known default value for each index.

## 1. Wi‑Fi card (driver advanced properties)

Applied via `Set-NetAdapterAdvancedProperty`; restored without a backup via `Reset-NetAdapterAdvancedProperty`. Property names differ by chip vendor → matched by `DisplayName` (regex); a tweak with no matching property is shown as "not supported".

| ID | Name | Target value | Risk | Notes |
|---|---|---|---|---|
| `wifi_power_saving` | Turn off card power saving | `Power Saving` = Disabled (MediaTek) · `MIMO Power Save Mode` = No SMPS (Intel/Realtek) | low | 🔌🛡 Common cause of intermittent disconnects |
| `wifi_wake_magic` | Turn off Wake on Magic Packet | Disabled | low | 🔌🛡 Prevents the card from being woken/hanging during sleep |
| `wifi_wake_pattern` | Turn off Wake on Pattern Match | Disabled | low | 🔌🛡 |
| `wifi_roaming` | Reduce roaming | `Roaming Aggressiveness` = Lowest (Intel) | low | 🔌🛡 Limits AP hopping when using mesh |
| `wifi_bw20_5g` | 5GHz bandwidth 20MHz only | `5GHz channel bandwidth` = 20MHz only | experimental | 🔌🛡 Lowers maximum speed in exchange for stability in noisy environments |
| `wifi_mode_ac` | Force Wi‑Fi 5 (802.11ac) | `802.11ax/ac/n/abg` = 802.11ac | experimental | 🔌🛡 Try when a Wi‑Fi 6 compatibility bug between card and router is suspected |

## 2. Windows power

| ID | Name | Target value | Risk | Notes |
|---|---|---|---|---|
| `device_power_off` | Do not let Windows turn off the card | Registry `PnPCapabilities` \|= `0x18` at the card's class key | low | 🔌🛡 Equivalent to unchecking "Allow the computer to turn off this device…" |
| `power_wireless_max` | Wireless Adapter: Maximum Performance | `powercfg` sub `19cbb8fa-…` / setting `12bbebe6-…` = 0 (AC & DC) | low | 🛡 Applies only to the current power plan |
| `power_pcie_aspm_off` | Turn off PCIe ASPM | `powercfg` sub `501a4d13-…` / setting `ee12f906-…` = 0 (AC & DC) | low | 🛡 Important for PCIe cards (MediaTek MT7921/7922). Slightly increases power consumption |

## 3. Network stack

| ID | Name | Target value | Risk | Notes |
|---|---|---|---|---|
| `tcp_timedwait` | Shorten TIME_WAIT | `HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\TcpTimedWaitDelay` = 30 | medium | 🔁🛡 Reduces port exhaustion errors (Tcpip event 4227). Original does not exist ⇒ restored by deleting the value |
| `ipv6_off` | Turn off IPv6 on Wi‑Fi | `Disable-NetAdapterBinding -ComponentID ms_tcpip6` | experimental | 🔌🛡 Only try when the router's IPv6/IPv6 DNS is suspected of causing slowness |

## 4. Measured tweaks

The value is computed from a measurement of the network in use, taken just before the tweak is turned on, and the measurement is kept with the backup ([ADR-0015](adr/0015-measured-tweaks.md)). Turning one on runs the measurement (real traffic, ~15 s), refuses when there is nothing to fix, applies, then measures again and records whether it helped (`tweak_verified` event). The UI shows the measurement behind the value and flags it when the PC is on another network or the measurement is older than 30 days; re-calibrating is turning the tweak off and on again. Nothing is re-measured or rewritten automatically.

| ID | Name | Target value | Risk | Notes |
|---|---|---|---|---|
| `upload_shaping` | Limit the upload speed to keep latency low under load | `NetQosPolicy` named `StableInternet-Upload`, `-Default` (all outbound traffic), `ThrottleRateActionBitsPerSecond` = 85% of the measured upload, rounded down to 0.1 Mbps, between 1 and 1000 Mbps | medium | 🛡 Upload only; the download direction can only be shaped on the router (SQM). Refused when latency under upload rises < 30 ms (nothing to fix), when the upload load is < 1 Mbps, or with too few latency samples |

`upload_shaping` details:

- **Measure**: the bufferbloat measurement of check #14 (`app/bufferbloat.py`) without its download phase: 4 s idle, then 10 s of upload over 4 connections while pinging 1.1.1.1 (and the router) every 0.2 s. Kept: upload Mbps, idle and loaded median latency to 1.1.1.1, number of samples, the network id (SHA-256 of the gateway's IPv4 + MAC, first 16 hex digits). It must be taken with the tweak off, otherwise it measures the tool's own limit.
- **Apply**: `New-NetQosPolicy -Name StableInternet-Upload -Default -ThrottleRateActionBitsPerSecond <cap>` in the default (persistent, `localhost`) policy store; when a policy with that name already exists, `Set-NetQosPolicy` changes its rate. Read back with `Get-NetQosPolicy` (works without Admin in the default store; the `ActiveStore` needs Admin, and `-PolicyStore PersistentStore` is not a NetQos store: "The network path was not found", checked 2026-10-07). Verified when the rate read back is within 1% of the cap.
- **Restore**: remove the policy with exactly that name from the default store, then from the `ActiveStore` if it is still there. No other policy is touched. Without a backup the restore is the same (the name belongs to the tool), so it is safe.
- **Prove**: after applying, the same measurement again; *helped* when the latency rise under upload dropped by ≥ 30% and ≥ 20 ms, otherwise the result suggests turning it off.
- The 2026-08-31 hand-made policy (15 Mbps) lowered the worst latency under upload from 1880 to 403 ms; it has been removed and was measured before the hardware fault was found, so it is a hint, not evidence.

## 5. Tool features

| ID | Name | Mechanism | Notes |
|---|---|---|---|
| `watchdog` | Self-healing watchdog | `settings.json` | See [WATCHDOG.md](WATCHDOG.md). Recovery actions need 🛡 |
| `failover` | Switch to a backup network path ([ADR-0008](adr/0008-failover.md)) | `settings.json` → `failover` (off by default). When switching: `Set-NetIPInterface -InterfaceIndex <backup> -AddressFamily IPv4 -InterfaceMetric <lower than the main path>`; back up the original `AutomaticMetric` + `InterfaceMetric` into `backup.json` under key `failover:<ifIndex>` before writing, read back to verify. Restore (`-AutomaticMetric Enabled` or the original metric) when the main path has been stable for 120s, when the feature is turned off, when the monitor starts and finds a leftover backup, on uninstall | 🛡 Without Admin ⇒ only notifies, switches when the user clicks (UAC) |
| `autostart` | Start with Windows | Task Scheduler, created from XML (not `schtasks /SC ONLOGON`, because its defaults stop the task after 72 hours and do not run it on battery): logon trigger (20s delay), no run-time limit, runs on battery too, restarts automatically up to 3 times if it crashes, runs hidden with `pythonw.exe` via `scripts/monitor/run_monitor.pyw`, does not open a browser. Defaults to `/RL LIMITED` because the monitor only reads; use `--highest` (`/RL HIGHEST`) when watchdog/tweaks need Admin rights. Remove: `python -m app.autostart uninstall --apply` | 🛡 only with `--highest` |

## Candidates not yet included (need evaluation)

- Change the DNS server based on benchmark results (for now only shown in Diagnostics).
- Limit Delivery Optimization / Windows Update background bandwidth.
- Turn off QoS Packet Scheduler, Network Throttling Index — unclear effect, easily becomes a "placebo tweak".
- Change driver (rollback) directly in the tool — high risk, currently only guided via Device Manager.

## Values on the development machine (2026-10-03)

| ID | Current | Recommendation |
|---|---|---|
| `wifi_power_saving` | Auto | Enable tweak |
| `wifi_wake_magic` / `wifi_wake_pattern` | Enabled | Enable tweak |
| `wifi_roaming` | — (card not supported) | — |
| `device_power_off` | `PnPCapabilities` = 16 (turning off allowed) | Enable tweak |
| `power_wireless_max` | AC 0 / DC 2 | Enable tweak (desktop, small impact) |
| `power_pcie_aspm_off` | AC **1 (Moderate)** / DC 2 | Enable tweak — high priority |
| `tcp_timedwait` | not set (default 120s) | Optional — 1 event 4227 already seen |

**Re-read with the tool on 2026-10-04** (`python -m app.tweaks list`, read-only): only `wifi_power_saving` is on; every other item is at its original value (Wake on = Enabled, `PnPCapabilities` = 16, ASPM AC 1 / DC 2, wireless AC 0 / DC 2); `wifi_roaming` not supported (the card has no such property). See EXPERIMENT-LOG, EXP-001.

Enable/disable from the command line (by default only prints the plan; Admin is required with `--apply`):

```powershell
python -m app.tweaks list
python -m app.tweaks enable power_pcie_aspm_off            # show the plan
python -m app.tweaks enable power_pcie_aspm_off --apply    # execute (PowerShell running as Admin)
python -m app.tweaks disable power_pcie_aspm_off --apply   # restore from data/backup.json
```
