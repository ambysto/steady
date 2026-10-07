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
| `tcp_ecn` | Turn on TCP ECN | `netsh int tcp set global ecncapability=enabled` | experimental | 🛡 Routers mark congestion instead of dropping packets, which can lower latency under load. Some middleboxes mishandle ECN packets, so keep it off unless a before/after measurement shows a gain. Windows default: `disabled` ⇒ restorable without a backup |
| `rsc_off` | Turn off Receive Segment Coalescing on the Wi‑Fi card | `Disable-NetAdapterRsc` (IPv4 and IPv6) | experimental | 🔌🛡 RSC glues received packets together before handing them to the stack: less CPU, a little more latency. Turning it off may lower throughput slightly. Restored from the backup only (the original IPv4/IPv6 flags); a card whose RSC is not available is "not supported" |
| `packet_coalescing_off` | Turn off the Packet Coalescing Filter | `Set-NetOffloadGlobalSetting -PacketCoalescingFilter Disabled` | experimental | 🛡 Stops the card holding back received packets to save power: lower latency, a little more power use. Restored from the backup only (the original `Default`/`Enabled`/`Disabled`): no known default |
| `dns_fastest` | Use the fastest public DNS over HTTPS | The fastest servers of a fixed public list (Cloudflare, Google, Quad9 — the providers Windows already knows a DoH template for), chosen by a benchmark at apply time ([ADR-0015](adr/0015-tweak-value-from-measurement.md)) | medium | 🛡 See below |

### `dns_fastest`

- **Candidates**: `1.1.1.1`, `1.0.0.1` (Cloudflare), `8.8.8.8`, `8.8.4.4` (Google), `9.9.9.9`, `149.112.112.112` (Quad9), all IPv4. Windows ships a DoH template for each (`Get-DnsClientDohServerAddress`).
- **Choice** (at apply time, never while only listing): every candidate is asked the benchmark names ([`app/dnsprobe.py`](../app/dnsprobe.py)); a server that failed any query is dropped. The providers are ranked by their fastest server. The result is the two best **different** providers: the winner's two servers (fastest first), then the runner-up's fastest. Fewer than two working providers ⇒ nothing is changed.
- **Apply**: `Set-DnsClientServerAddress -InterfaceIndex <uplink> -ServerAddresses …` (IPv4 only), then for each chosen address `Set-DnsClientDohServerAddress -AutoUpgrade $true -AllowFallbackToUdp $true` (the address is added with its template first if Windows lacks it). The fallback to plain UDP is kept so DNS does not die on a network that blocks DoH.
- **Backup**: whether the interface's IPv4 DNS was static (`NameServer` in `Tcpip\Parameters\Interfaces\{guid}`) or from DHCP, its servers, and the DoH flags of **all six candidates** (the chosen ones are not known until apply).
- **Restore**: DHCP original ⇒ `Set-DnsClientServerAddress -ResetServerAddresses`; static original ⇒ the same servers again. Each candidate's DoH flags go back to what they were (an entry that did not exist is removed). Without a backup nothing is guessed: it is refused.
- **Enabled** means: the interface's IPv4 DNS is static, all its servers are candidates from at least two providers, and each has DoH auto-upgrade on. So a hand-made setup like the one of 2026-08-31 counts as on, and enabling it is a no-op.
- **Not supported** (and so never suggested) while: a VPN adapter is up, the PC is on a domain, the interface has a DNS suffix (an internal domain whose names only the DHCP DNS can resolve), it has a static IPv6 DNS (restoring with a reset would wipe it), or the network answers the connectivity probe like a captive portal.
- **dnswatch**: the monitor does not report a DNS change as unusual when the app changed DNS in the last 5 minutes (`tweak_enabled` / `tweak_disabled` event of `dns_fastest`); it records it as `dns_observed` instead.

## 4. Tool features

| ID | Name | Mechanism | Notes |
|---|---|---|---|
| `watchdog` | Self-healing watchdog | `settings.json` | See [WATCHDOG.md](WATCHDOG.md). Recovery actions need 🛡 |
| `failover` | Switch to a backup network path ([ADR-0008](adr/0008-failover.md)) | `settings.json` → `failover` (off by default). When switching: `Set-NetIPInterface -InterfaceIndex <backup> -AddressFamily IPv4 -InterfaceMetric <lower than the main path>`; back up the original `AutomaticMetric` + `InterfaceMetric` into `backup.json` under key `failover:<ifIndex>` before writing, read back to verify. Restore (`-AutomaticMetric Enabled` or the original metric) when the main path has been stable for 120s, when the feature is turned off, when the monitor starts and finds a leftover backup, on uninstall | 🛡 Without Admin ⇒ only notifies, switches when the user clicks (UAC) |
| `autostart` | Start with Windows | Task Scheduler, created from XML (not `schtasks /SC ONLOGON`, because its defaults stop the task after 72 hours and do not run it on battery): logon trigger (20s delay), no run-time limit, runs on battery too, restarts automatically up to 3 times if it crashes, runs hidden with `pythonw.exe` via `scripts/monitor/run_monitor.pyw`, does not open a browser. Defaults to `/RL LIMITED` because the monitor only reads; use `--highest` (`/RL HIGHEST`) when watchdog/tweaks need Admin rights. Remove: `python -m app.autostart uninstall --apply` | 🛡 only with `--highest` |

## Candidates not yet included (need evaluation)

- Limit Delivery Optimization / Windows Update background bandwidth.
- Turn off QoS Packet Scheduler, Network Throttling Index — unclear effect, easily becomes a "placebo tweak".
- Not planned, placebo: `NonBestEffortLimit=0`, turning TCP timestamps off, `InitialRto`. Each of the experimental tweaks above (`tcp_ecn`, `rsc_off`, `packet_coalescing_off`) is off by default and only worth keeping if a before/after measurement shows a gain.
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

**Read with the tool on 2026-10-07** (read-only, after the manual session of 2026-08-31): `tcp_ecn` on (`enabled`), `packet_coalescing_off` on (`Disabled`), `dns_fastest` on (static `1.1.1.1`, `1.0.0.1`, `8.8.8.8`, DoH auto-upgrade on for all three), `rsc_off` **not supported** (the card's RSC capability is false for both IPv4 and IPv6, so there is nothing to turn off). Enabling any of these is a no-op; disabling `tcp_ecn` goes back to Windows' `disabled`, the others have no backup and are refused rather than guessed.

**Re-read with the tool on 2026-10-04** (`python -m app.tweaks list`, read-only): only `wifi_power_saving` is on; every other item is at its original value (Wake on = Enabled, `PnPCapabilities` = 16, ASPM AC 1 / DC 2, wireless AC 0 / DC 2); `wifi_roaming` not supported (the card has no such property). See EXPERIMENT-LOG, EXP-001.

Enable/disable from the command line (by default only prints the plan; Admin is required with `--apply`):

```powershell
python -m app.tweaks list
python -m app.tweaks enable power_pcie_aspm_off            # show the plan
python -m app.tweaks enable power_pcie_aspm_off --apply    # execute (PowerShell running as Admin)
python -m app.tweaks disable power_pcie_aspm_off --apply   # restore from data/backup.json
```
