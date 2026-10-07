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
| `wifi_prefer_5g` | Prefer the 5 GHz band | `Preferred Band` / `Band Preference` = Prefer 5GHz (never "5G only") | low | 🔌🛡 A card that clings to a 2.4 GHz access point (802.11n, 65 Mbps link, 5% router loss) moved to 5 GHz 802.11ac (175 Mbps, 0% loss) when this was set. It only prefers 5 GHz: the card still falls back to 2.4 GHz where 5 GHz is out of range, which is why "5G only" is not used (some drivers ignore it anyway). Offered only while the card is on a 2.4 GHz access point and the connected network has a 5 GHz one in Windows' last scan; otherwise it is shown as not supported, with the reason. Not offered on a 6 GHz connection (Wi‑Fi 6E/7): preferring 5 GHz could pull the card down from 6 GHz |
| `wifi_tx_power_max` | Highest transmit power | `Transmit Power` / `Transmit Power Level` / `Tx Power` = Highest | low | 🔌🛡 Some drivers lower the transmit power on their own to save energy; this keeps the card at its top level. Slightly more heat and battery use on a laptop |

Matching rules for both: the property is found by `DisplayName` and the value by `DisplayValue`, each a full-match regular expression that tolerates a numeric prefix such as `3. ` (Intel and MediaTek add one). A card with no such property, or with no matching value, is shown as "not supported". Restore uses the value saved in `backup.json`, or `Reset-NetAdapterAdvancedProperty` (the driver default) when there is no backup.

**On by the driver default.** A property can already hold the target value because the driver ships that way (the MediaTek card's `PreferredBand` and `TxPowerLevel` defaults are `Prefer 5GHz` and `Highest`). Without a backup that is not a change the tool made, so the tweak is shown as on *by the driver default*, its switch is disabled, and turning it off writes nothing: `Reset-NetAdapterAdvancedProperty` would only restart the adapter and leave the same value. When a backup exists (the tool did write it) it is restored as usual. After any reset to the default the value is read back and a value that did not change is reported as a failure.

How `wifi_prefer_5g` reads the connection (read-only, never triggers a scan): from the `netsh wlan show interfaces` entry whose name is the tweak's adapter, the SSID, the BSSID and the `Band` field (the band the card is on now; when `Band` is missing, the band of the connected BSSID in the scan); then the bands of the same network's access points in `netsh wlan show networks mode=bssid`, matched by SSID or by the connected BSSID (a hidden SSID is not listed under its name). A channel number alone does not tell 2.4 GHz from 6 GHz (6 GHz uses channels 1, 5, 9, 13…), so the band always comes from a `Band` field. It is not offered when: not connected to Wi‑Fi; the current band cannot be told; the card is not on 2.4 GHz (already 5 GHz, or 6 GHz); the network is not in the last scan (the bands are unknown); or the network has no 5 GHz access point. When the property already holds the target value the tweak is shown as on whatever the network looks like, so it can still be turned off.

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
| `tcp_ecn` | Turn on TCP ECN | `netsh int tcp set global ecncapability=enabled` (read back from `Get-NetTCPSetting -SettingName Internet`, whose property names are not translated like the `netsh show` labels) | experimental | 🛡 Routers mark congestion instead of dropping packets, which can lower latency under load. Some middleboxes mishandle ECN packets, so keep it off unless a before/after measurement shows a gain. Without a backup, turning it off sets `ecncapability=default` (Windows' own choice), never a value of this tool's choosing; with a backup the original comes back |
| `rsc_off` | Turn off Receive Segment Coalescing on the Wi‑Fi card | `Disable-NetAdapterRsc` (IPv4 and IPv6) | experimental | 🔌🛡 RSC glues received packets together before handing them to the stack: less CPU, a little more latency. Turning it off may lower throughput slightly. Restored from the backup only (the original IPv4/IPv6 flags); a card whose RSC is not available is "not supported" |
| `packet_coalescing_off` | Turn off the Packet Coalescing Filter | `Set-NetOffloadGlobalSetting -PacketCoalescingFilter Disabled` | experimental | 🛡 Stops the card holding back received packets to save power: lower latency, a little more power use. Restored from the backup only (the original `Default`/`Enabled`/`Disabled`): no known default |
| `dns_fastest` | Use the fastest public DNS over HTTPS | The fastest servers of a fixed public list (Cloudflare, Google, Quad9 — the providers Windows already knows a DoH template for), chosen by a benchmark at apply time ([ADR-0015](adr/0015-tweak-value-from-measurement.md)) | medium | 🛡 See below |

### `dns_fastest`

- **Candidates**: `1.1.1.1`, `1.0.0.1` (Cloudflare), `8.8.8.8`, `8.8.4.4` (Google), `9.9.9.9`, `149.112.112.112` (Quad9), all IPv4. Windows ships a DoH template for each (`Get-DnsClientDohServerAddress`).
- **Choice** (at apply time, never while only listing): every candidate is asked the benchmark names ([`app/dnsprobe.py`](../app/dnsprobe.py)); a server that failed any query is dropped. The providers are ranked by their fastest server. The result keeps two **different** providers in its first two places: the winner's fastest server, the runner-up's fastest, then the winner's second server if it works. Fewer than two working providers ⇒ nothing is changed.
- **Apply**, in this order: for each chosen address `Set-DnsClientDohServerAddress -AutoUpgrade $true -AllowFallbackToUdp $true` (the address is added with its template first if Windows lacks it), **then** `Set-DnsClientServerAddress -InterfaceIndex <uplink> -ServerAddresses …` (IPv4 only), so Windows already knows to upgrade the moment a server is used. The fallback to plain UDP is kept so DNS does not die on a network that blocks DoH.
- **Backup**: the interface's **InterfaceGuid** (an ifIndex can be reused, a GUID cannot), whether its IPv4 DNS was static (`NameServer` in `Tcpip\Parameters\Interfaces\{guid}`) or from DHCP, its servers, and the DoH flags of **all six candidates** (the chosen ones are not known until apply). It belongs to that one interface: turning the tweak off and checking the result look at that interface, not at whichever one is the uplink by then (a dock's Ethernet, a VPN, no route at all), and turning it on while the backup is another interface's is refused until it has been turned off. So that the switch can do that, the state of a tweak with a saved backup that `read()` cannot see (the uplink is another interface, or there is no route at all; the adapter of the backup still exists) is **on** with the warning "On for another network connection (…), not the one in use now" (`Tweak.backup_reading`); clicking the switch restores that interface. If its adapter is gone nothing is offered and `disable` says so.
- **Restore**: DHCP original ⇒ `Set-DnsClientServerAddress -ResetServerAddresses`; static original ⇒ the same servers again. Each DoH entry apply could have changed goes back to what it was (an entry that did not exist is removed). An entry that somebody else has changed since (flags that are not the ones apply sets) is left alone, an entry that has vanished is put back only if it is one of the servers apply set, and nothing that is already as it was gets rewritten. Without a backup nothing is guessed: it is refused. If the interface is gone (an unplugged dongle) it says so and keeps the backup.
- **Enabled** means: the interface's IPv4 DNS is static, all its servers are candidates from at least two providers, and each has DoH auto-upgrade on. So a hand-made setup like the one of 2026-08-31 counts as on, and enabling it is a no-op. **While it is on and the network is one it would not have been turned on for** (a VPN came up, the PC joined a domain, a DNS suffix or a captive portal appeared), the state carries a **warning** shown beside the switch ("Worth a look"): the static DNS stays with the interface, not with the network, so a network that needs its own DNS keeps the public one until the tweak is turned off. The captive-portal answer is reused for a minute between listings; turning it on always asks again.
- **Not supported** (and so never suggested) while this Windows has no DoH cmdlets (Windows 10 before 21H2) or while: a VPN adapter is up, the PC is on a domain, the interface has a DNS suffix (an internal domain whose names only the DHCP DNS can resolve), it has a static IPv6 DNS (restoring with a reset would wipe it), or the network answers the connectivity probe like a captive portal.
- **dnswatch**: the monitor does not report a DNS change as unusual when the app changed DNS in the last 5 minutes (`tweak_enabled` / `tweak_disabled` / `tweak_failed` event of `dns_fastest`); it records it as `dns_observed` instead. A failed change (`tweak_failed`) counts too, because it may have moved DNS before it failed.

## 4. Tweaks calibrated by a measurement

The value is computed from a measurement of the network in use, taken before the tweak is turned on (unlike `dns_fastest`, whose benchmark runs inside apply, ADR-0015), and the measurement is kept with the backup ([ADR-0016](adr/0016-measured-tweaks.md)). Turning one on runs the measurement (real traffic, ~15 s), refuses when there is nothing to fix, applies, then measures again and records whether it helped (`tweak_verified` event). The UI shows the measurement behind the value and flags it when the PC is on another network or the measurement is older than 30 days; re-calibrating is turning the tweak off and on again. Nothing is re-measured or rewritten automatically.

| ID | Name | Target value | Risk | Notes |
|---|---|---|---|---|
| `upload_shaping` | Limit the upload speed to keep latency low under load | `NetQosPolicy` named `StableInternet-Upload`, `-Default` (all outbound traffic), `ThrottleRateActionBitsPerSecond` = 85% of the measured upload, rounded down to 0.1 Mbps, between 1 and 1000 Mbps; plus one unthrottled policy per local network (`StableInternet-Upload-Local1…6`) so the LAN is not limited | medium | 🛡 Upload to the Internet only (local networks are exempted); the download direction can only be shaped on the router (SQM). Refused when latency under upload rises < 30 ms (nothing to fix), when the upload load is < 1 Mbps, when the measurement reached its own ceiling (≥ 95% of the 80 Mbps a 100 MB, 10 s phase can show: the line may be faster), or with too few latency samples |

`upload_shaping` details:

- **Measure**: the bufferbloat measurement of check #14 (`app/bufferbloat.py`) without its download phase: 4 s idle, then 10 s of upload over 4 connections while pinging 1.1.1.1 (and the router) every 0.2 s. Kept: upload Mbps, idle and loaded median latency to 1.1.1.1, number of samples, the network id (SHA-256 of the gateway's IPv4 + MAC, first 16 hex digits). It must be taken with the tweak off, otherwise it measures the tool's own limit.
- **Counting samples**: the first 2 s of the load phase are dropped by time (on a bloated line one round of pings can take most of a second, so dropping a fixed number of rounds would drop most of the phase). A ping lost under load counts as the 900 ms timeout, a lower bound, in the loaded median: on a badly bloated line the slowest pings are the lost ones. "Too few samples" means too few pings *attempted* (< 8), not answered.
- **Apply**: `New-NetQosPolicy -Name StableInternet-Upload -Default -ThrottleRateActionBitsPerSecond <cap>` in the default (persistent, `localhost`) policy store; when a policy with that name already exists, `Set-NetQosPolicy` changes its rate. Read back with `Get-NetQosPolicy` (works without Admin in the default store; the `ActiveStore` needs Admin, and `-PolicyStore PersistentStore` is not a NetQos store: "The network path was not found", checked 2026-10-07). Verified when the rate read back is within 1% of the cap. On the dev PC (EXP-015) the rate read back was exactly the one written, and the policy appeared in the `ActiveStore` at once.
- **Local networks**: `-Default` matches every outbound flow, LAN included (a NAS copy would drop to the Internet uplink's speed). Before the throttle, one policy per local destination is created with `New-NetQosPolicy -Name StableInternet-Upload-LocalN -IPDstPrefixMatchCondition <prefix> -DSCPAction 0` for `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `fc00::/7`, `fe80::/10`: of several matching policies Windows applies the most specific, and these throttle nothing (DSCP 0 is the unmarked default; a policy needs one action). The tweak counts as applied only when all six are present. Not yet checked on a real machine that the exemption wins over `-Default` (a LAN copy at full speed with the limit on).
- **Restore**: remove the throttle policy first (default store, then the `ActiveStore` if it lingers), then the exemptions; only names starting with `StableInternet-Upload` are touched, and a backup naming another policy is refused. Without a backup the restore is the same (the names belong to the tool), so it is safe.
- **Prove**: after applying, the same measurement again; *helped* when the latency rise under upload dropped by ≥ 30% and ≥ 20 ms, otherwise the result suggests turning it off.
- **While on**: check #14 says the upload is limited by the app to X Mbps, and when the measured upload reaches 95% of that limit (a faster plan, for example) asks to turn it off and on again to re-measure. A limit that is on with no measurement on record (the elevated helper stopped between applying and saving it) is flagged "No measurement on record". A measured enable and check #14 never run at the same time (one lock): each would see about half the bandwidth.
- The 2026-08-31 hand-made policy (15 Mbps) lowered the worst latency under upload from 1880 to 403 ms; it has been removed and was measured before the hardware fault was found, so it is a hint, not evidence.

## 5. Tool features

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
| `wifi_prefer_5g` | `PreferredBand` = `3. Prefer 5GHz band` (read 2026-10-07; the driver default, values `1. No Preference` / `2. Prefer 2.4GHz band` / `3. Prefer 5GHz band`), so on by the driver default | Nothing to do on this card; it was the change that helped most on 31 Aug (see the tweak's notes above) |
| `wifi_tx_power_max` | `TxPowerLevel` = `1. Highest` (read 2026-10-07; the driver default, values `1. Highest` / `2. Medium` / `3. Lowest`), so on by the driver default | Nothing to do; the tweak only guards against a driver or a reset lowering it |
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
