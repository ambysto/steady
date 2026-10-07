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
| `wifi_prefer_5g` | Prefer the 5 GHz band | `Preferred Band` = Prefer 5GHz band (MediaTek/Intel) · `Band Preference` = Prefer 5G (Realtek) | low | 🔌🛡 A preference, not "5 GHz only" (several drivers ignore band-only settings, and forcing a band can strand the PC on a 2.4 GHz-only network). Only helps when the router broadcasts the same network on 5 GHz; 5 GHz reaches less far through walls. On the development PC, moving from 2.4 GHz 802.11n (65 Mbps link) to 5 GHz 802.11ac (175 Mbps) took router packet loss from 5% to 0% |
| `wifi_tx_power_max` | Highest transmit power | `Transmit Power Level` = Highest (MediaTek) · `Transmit Power` = Highest (Intel/Realtek) | low | 🔌🛡 Helps only if the driver or BIOS had lowered it; most cards already default to the highest (then the tweak shows as already on) |

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

## 4. Tool features

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
