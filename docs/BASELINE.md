# Initial state (Baseline)

Data measured on the development machine **before applying any optimization**. Used as a reference point for comparison after each change.

- **Measured on:** 2026-10-03
- **Machine:** Windows 11 Pro 10.0.26200, desktop
- **Method:** ping 60 packets / target, 150ms apart; event log for the last 7 days

## Network diagram

```
ISP ── ISP modem (bridge mode) ──cable── Huawei WiFi BE3 (PPPoE, DHCP, broadcasts Wi‑Fi) ~~5GHz~~ PC (MediaTek MT7922)
```

*(Update 14:20: the actual model is the **华为路由 BE3 Pro 雷电版**, the mainland China version, firmware 6.1.0.11, HarmonyOS 6.1.0. The specifications below are for the international BE3 version, for reference only.)*

Huawei WiFi BE3: Wi‑Fi 7 dual-band 2×2 (BE3600), 1 × 2.5G port + 3 × 1G ports (WAN/LAN auto-detect), supports HUAWEI Mesh+ and 802.11k/v/r.

**Update 2026-10-03 11:45:** the user already has a **VNPT iGate 302S mesh** but it is **not turned on** (unclear whether it is compatible with the BE3; no public specifications for this model found yet). The PC is connected **directly to the BE3**. The BSSID `02:5e:00:9a:40:24` differs from the BE3's LAN MAC (`64:A2:8A:00:00:01`) only because the BE3 uses a locally-administered address for its 5GHz radio. *(The earlier inference that the PC went through the iGate was wrong.)*

⇒ The "router connection lost" events in EXP-001 are on the **Wi‑Fi PC ↔ BE3** hop.

## Hardware & connection

| Item | Value |
|---|---|
| Wi‑Fi card | TP-Link Wi-Fi 6 PCIe Adapter — **MediaTek MT7922** chip (`PCI\VEN_14C3&DEV_7922`) |
| Driver in use | MediaTek 3.6.0.1434 (2026-07-01), `oem35.inf` |
| Other driver in the driver store | MediaTek 25.40.2.585 (2025-12-05), `oem18.inf` — candidate for a rollback test |
| Wired network card | Intel I219-V — **Disconnected** (no cable plugged in) |
| Virtual adapters | WireGuard `wt0` (NetBird, running) · OpenVPN DCO (Surfshark, not connected) · Bluetooth PAN |
| SSID / band | "HomeNet" · 5GHz · channel 36 · 802.11ax · WPA2 |
| Signal | 75% · RSSI **−66 dBm** · Rx 216 / Tx 360 Mbps |
| Channel interference | 2 other networks on channel 36 (57–60%) |
| Gateway | 192.168.3.1 |
| DNS (IPv4) | 1.1.1.1, 1.0.0.1, 8.8.8.8 (static) · IPv6: the router's link-local DNS |

## Link quality

| Target | Packet loss | Average | Min / Max | Jitter |
|---|---|---|---|---|
| Router 192.168.3.1 | 0/60 | 5.4 ms | 3 / 36 | 2.8 ms |
| Cloudflare 1.1.1.1 | 0/60 | 55.9 ms | 52 / 87 | 3.8 ms |
| Google 8.8.8.8 | 0/60 | 92.2 ms | 40 / 300 | **37.8 ms** |

DNS (A query, ms):

| Server | facebook.com | youtube.com |
|---|---|---|
| Router 192.168.3.1 | 5 | 9 |
| 1.1.1.1 | 660 | 52 |
| 8.8.8.8 | 62 | 51 |

One resolution of facebook.com through the system resolver took ~30s (timeout); repeating it was normal.

## Disconnection history (7 days: 2026-09-26 → 2026-10-02)

**47** Wi‑Fi disconnect events (event 8003):

| Reason | Count |
|---|---|
| Disconnected by the driver | **28** |
| User wants to establish a new connection | 12 |
| Disconnected by the user | 5 |
| Temporary-disconnect request | 2 |

By day: 09-26: 1 · 09-27: 3 · 09-28: 1 · 10-01: 6 · **10-02: 36**

Related events (System log):

| Source | ID | Count | Meaning |
|---|---|---|---|
| WLAN-AutoConfig | 10002 | 16 | IHV module `mtkihvx.dll` (MediaTek driver) stopped |
| WLAN-AutoConfig | 4003 | 9 | "limited connectivity" detected, Windows recovered on its own |
| Tcpip | 4227 | 13 | Local port exhaustion/reuse (TIME_WAIT: 358 connections at measurement time) |
| e1dexpress | 27 | 14 | Wired network cable disconnected (normal since nothing is plugged in) |

## Power & driver configuration

| Setting | Value |
|---|---|
| Power plan | Balanced |
| Wireless Adapter Power Saving | AC 0 (Max Performance) / DC 2 |
| PCIe Link State Power Management (ASPM) | **AC 1 (Moderate)** / DC 2 |
| Driver `Power Saving` | **Auto** |
| Wake on Magic Packet / Pattern Match | Enabled / Enabled |
| `PnPCapabilities` | 16 (Windows is allowed to turn off the card) |
| `TcpTimedWaitDelay` | not set (default) |

## Preliminary conclusions

1. Main cause: **MediaTek driver crashes** (10002) coinciding with "disconnected by the driver".
2. Contributing factors: PCIe ASPM and the card's power saving are enabled; the signal is average; channel 36 is shared.
3. The ISP link is stable (0% packet loss); the high jitter to 8.8.8.8 is due to the ISP's routing.
4. The most thorough solution: use a wired network (the Intel I219-V port is unused).
