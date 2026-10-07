# Experiment log

Records every configuration change made **by hand** on the machine (before the tool existed), with before/after values, how to restore, and observed results. Each entry is one experiment; only one group of factors is changed at a time so we know what has an effect.

**Main metrics for evaluation** (compared with [BASELINE.md](BASELINE.md)):

| Metric | Source | Baseline (7 days before 2026-10-03) |
|---|---|---|
| Number of "disconnected by the driver" / 24h | Event 8003 | 4.0 (28 times / 7 days; 10-02 alone: more than 20) |
| Number of MediaTek IHV module stops / 24h | Event 10002 | 2.3 (16 / 7 days) |
| Packet loss to router | `ping-logger` | 0% (short sample of 60 packets) |
| Number of router connection losses (≥ 3s) | `ping-logger` | no data yet |

Quick view: `powershell -ExecutionPolicy Bypass -File scripts\monitor\report.ps1`

---

## EXP-001 — Disable the power-saving mechanisms of the Wi‑Fi card and PCIe

- **Applied at:** 2026-10-03 10:59:52 (+07)
- **Done with:** [scripts/manual/apply-lowrisk.ps1](../scripts/manual/apply-lowrisk.ps1) (Admin rights)
- **Backup:** `data/manual/backup-20261003-105949.json` · log: `data/manual/apply-20261003-105949.log`
- **Hypothesis:** the MediaTek MT7922 driver hangs/crashes when the card or the PCIe slot enters a power-saving state ⇒ disabling these mechanisms will sharply reduce the number of "disconnected by the driver" and event 10002 occurrences.

| Tweak ID | Setting | Before | After |
|---|---|---|---|
| `wifi_power_saving` | Driver: Power Saving | Auto | **Disabled** |
| `wifi_wake_magic` | Driver: Wake on Magic Packet | Enabled | **Disabled** |
| `wifi_wake_pattern` | Driver: Wake on Pattern Match | Enabled | **Disabled** |
| `device_power_off` | Registry `PnPCapabilities` (class key `…\0011`) | 16 | **24** |
| `power_wireless_max` | Power plan: Wireless Adapter Power Saving (AC / DC) | 0 / 2 | **0 / 0** |
| `power_pcie_aspm_off` | Power plan: PCIe Link State Power Management (AC / DC) | **1 (Moderate)** / 2 | **0 / 0** (Off) |

Power plan: Balanced (`381b4222-…`). Power plan changes only take effect for this plan.

**Not applied this time** (to isolate the impact): `wifi_bw20_5g`, `wifi_mode_ac`, `ipv6_off` (experimental), `tcp_timedwait` (medium, needs reboot), changing the router channel, driver rollback.

**Results right after applying** (11:00, 60 packets / target):

| Target | Packet loss | Avg | Max | Jitter |
|---|---|---|---|---|
| Router | 0/60 | 5.0 ms | 48 | 2.0 ms |
| 1.1.1.1 | 0/60 | 68.2 ms | 231 | 11.7 ms |
| 8.8.8.8 | 0/60 | 44.0 ms | 98 | 4.2 ms |

Wi‑Fi reconnected ~9s after the card restarted; signal 72% / −65 dBm, channel 36, Rx 204 / Tx 360 Mbps. The immediate results are no different from the baseline — as expected, since the problem is **intermittent** drops; multiple days of monitoring are needed.

**Monitoring:** `ping-logger` running in the background since 2026-10-03 ~11:02 (no Admin rights, stops on sign-out/restart).

> **From 2026-10-04 10:05:** the PowerShell logger has been stopped and removed from Startup; replaced by the Python monitor (`python -m app.monitor`, run via Task Scheduler "StableInternet Monitor", since 09:59). New data lives in `data/metrics.db` (SQLite), no longer in `data/monitor/*.csv`. The old CSVs are kept as is and can be imported with `Storage.import_csv_dir()`. `report.ps1` only reads CSV, so it no longer sees new data — use `python -m app.diagnostics` or query `metrics.db`.

**Evaluation milestones:**
- [ ] 2026-10-04 (24h) — run `report.ps1`, record the results below
- [ ] 2026-10-06 (72h) — conclusion: keep / restore / move to EXP-002

**Restore:** run with Admin rights
```powershell
powershell -ExecutionPolicy Bypass -File scripts\manual\restore.ps1 -Backup data\manual\backup-20261003-105949.json
```

> ⚠️ **Re-checked 2026-10-04 ~09:55 (read-only):** most of EXP-001 is **no longer applied**. Only `Power Saving` is still `Disabled`; `Wake on Magic Packet` / `Wake on Pattern Match` = `Enabled`, `PnPCapabilities` = 16, Wireless Adapter AC 0 / **DC 2**, PCIe ASPM **AC 1 / DC 2** — exactly the values from *before* applying. Confirmed in two independent ways (`apply-lowrisk.ps1 -ShowState` and the new tweak framework); the card is still under class key `0011`, so the driver was not reinstalled. `data/manual/` has no restore log ⇒ **unknown when and why it was reverted**. Consequence: the good results after EXP-014 (rotating the PC case) were achieved when most of EXP-001 was already gone — further reinforcing the conclusion that the main cause was the blocked antennas.

**Observations:**
- **10:59:51** — Windows logged 1 event 8003 + 1 event 10002 + `mtkwlex` events 1033/8001/8000: this is due to **deliberately** restarting the card when applying, *not counted* as an incident. Keep in mind when reading `report.ps1` (the default start mark is 10:59:49, so it will count these 2 events).
- **11:03:59 → 11:04:56 — lost connection to the router for 57 seconds** (logger: router lost 37/42 packets in minute 11:04) but **Wi‑Fi still reported `connected`**, same BSSID, no 8003/10002/4003 events in Windows. ⇒ **"silent hang"**: the link is kept but the data path stalls. Windows does not detect/recover on its own ⇒ reinforces the need for a watchdog based on pinging the router (exactly the `router_unreachable` scenario in [WATCHDOG.md](WATCHDOG.md)).
  - Right before that, the Rx rate dropped sharply: 6 Mbps (11:03), 34 Mbps (11:04), then recovered 122 → 360 Mbps. ⇒ A sudden Rx rate drop may be an **early warning signal** — consider adding it to diagnostics / the watchdog.
  - Happened only ~4 minutes after applying ⇒ EXP-001 has **not** eliminated the hang; more data is needed to know whether the frequency has decreased.
- **Report for the first 15 minutes (10:59 → 11:18):** router lost **10%** of packets (82/817), 7/15 minutes had packet loss; **4 router connection losses**: 57s (11:04), 25s (11:11), 3s (11:15), 27s (11:17). Wi‑Fi always `connected`, no roaming, no Windows events.
- **Pattern:** every connection loss coincided with a minute where the **Rx rate collapsed to 6 Mbps** (the lowest MCS level) — 11:03, 11:10, 11:17 — while the **signal was still 71–75% / −67 dBm**. Strong enough signal yet collapsed rate ⇒ leans toward **interference / contention on channel 36** (2 neighbor networks on the same channel), or a fault on the AP side (BSSID `02:79:…` is a locally-administered address — possibly a mesh node/virtual AP), rather than power saving.
- ⚠️ **Methodological limitation:** the logger only started running *after* EXP-001 was applied, so **there is no ping data with the same method for the earlier period** ⇒ we cannot yet say whether EXP-001 made things better or worse. Lesson: always run the logger before making a change to get a baseline with the same yardstick.
- 11:25 — logger **runs automatically at sign-in**: shortcut in the Startup folder → `scripts/monitor/start-logger-hidden.vbs` (install/remove: `scripts/monitor/install-autostart.ps1 [-Remove]`).
- 11:21–11:30 — logger PID 27080 was stopped unexpectedly (no `logger_stop` ⇒ killed from outside, suspected to be a child process of the Claude session getting cleaned up) ⇒ **lost data for 11:21–11:24 and 11:26–11:29**. Restarted detached from the session via WMI (PID 28344, 11:30).
- 11:20 — fixed the `internet_down` classification bug in the logger (previously it could wrongly record "router reachable" when the router was also lost in the same incident); restarted the logger.
- **11:05 → 11:08** — router 0% packet loss, but 1.1.1.1 and 8.8.8.8 lost **3–17%** per minute (2–10 / 60 packets) despite normal latency. Different from the 60-packet sample at 11:00 (0%). Hypothesis to verify: packet loss on the router's WAN side/ISP, or the router/ISP rate-limiting ICMP. ⇒ The logger should record per second (or add TCP/DNS checks) to distinguish real packet loss from rate-limited ICMP.

---

### EXP-001 progress: 11:30 → 13:14 (before changing the channel)

`report.ps1 -Since '2026-10-03 11:30'` (107 minutes):

| Target | Packet loss | Avg | Jitter | Minutes with packet loss |
|---|---|---|---|---|
| Router | **34.9%** (1920/5503) | 25.7 ms | 31.7 ms | **101/107** |
| 1.1.1.1 | 43.3% | 80.6 ms | 36.6 ms | 107/107 |
| 8.8.8.8 | 42.3% | 72.9 ms | 41.7 ms | 107/107 |

- **64 router connection losses**, total **2519s (~42 minutes / 107 minutes)**. Windows logged only 1 "disconnected by the driver" ⇒ almost all are **silent hangs**.
- Rx rate collapsed to **6 Mbps** in most minutes (8–14 minutes in each 15-minute window).
- Signal gradually decreasing: 71% / −69 dBm (11:30–12:30) → 63% / −73 (12:45) → 57% / −76 (13:00). Cause unclear (need to ask: was anything changed at the router/location around ~12:45?).
- ⚠️ The situation is **clearly worse** than in the first 15 minutes (10% packet loss). We have not ruled out that EXP-001 (disabling power saving on the MediaTek) made things worse ⇒ an A/B (EXP-001b) is all the more necessary.

## EXP-002 — Change the BE3's 5GHz channel: 36 → 157

- **When:** ~13:14–13:16 (+07) — the user changed it on the BE3 admin page (disabled 自动优化信道, set 组网信道模式, chose the channel manually). The logger saw the PC temporarily roam to 2.4G ch1 (13:14:33), to 5G ch44 (13:15:57), and finally ch157.
- **Kept unchanged:** EXP-001 still applied; 5G bandwidth 20/40/80 MHz.
- **Hypothesis:** leaving channel 36 (shared with 2 neighbor networks) will end the Rx-collapsing-to-6-Mbps phenomenon and the connection losses.
- **Right after the change (13:17):** channel 157, **signal 48% / −76 dBm** (weak), Rx 367 / Tx 432 Mbps. Channel 157 has no neighbor networks; channel 36 still has 2 networks (c4/c6:2c:7b…), channel 48 has 2 networks.
- **Note:** high channels (UNII‑3) attenuate a bit more through walls; if RSSI stays below −72 dBm, consider channel 149 or the 穿墙 (wall penetration) transmit power mode.
- **Evaluation milestones:** 13:45 and 15:00 — compare packet loss to the router, number of connection losses, and number of minutes with Rx ≤ 6 Mbps against the 11:30–13:14 window above.

**Observations:**
- **13:17 → 13:33 (16 minutes):** router lost **19.7%** of packets (12/16 minutes had packet loss), 6 router connection losses (235s); Internet lost ~65%. First appearance of **real disconnects** (Wi‑Fi `disconnected` 13:29:41, 13:31:51; 5 events 8003, 1 event 10002, 1 event 4003). RSSI **−75 → −79 dBm**; Rx still collapsed to 6 Mbps at 13:20–13:21. For 1 minute the PC was on channel 36 (13:22).
- Preliminary assessment: leaving channel 36 has **not** solved it; the prominent problem right now is **weak signal** (−76…−79), which had already been dropping since ~12:45 while still on channel 36 ⇒ not caused by the channel change. Need to ask the user whether they were working on the router at 13:29–13:33.

## Finding: the ISP modem still broadcasts Wi‑Fi even though it is bridged (13:40)

- User: the ISP modem has been switched to **bridge**, "OldModemNet" is the old network; **the modem and the BE3 are placed right next to each other**.
- The SSIDs OldModemNet / CNBN / NeighborNet / a hidden SSID share the MAC family `c4/c6:2c:7b:…` ⇒ most likely **all broadcast by the old modem** (bridge only disables the routing function, not the Wi‑Fi radio). They occupy **5G channel 36** and **2.4G channel 4**.
- Two transmitters placed right next to each other ⇒ (a) channel contention when on the same channel; (b) **blocking/desensitizing the BE3's receiver** even on different channels, because the modem's signal is too strong at a distance of a few cm.
- Proposal: turn off Wi‑Fi on the modem (WLAN button / admin page via the OldModemNet SSID / ask the ISP hotline) + place the BE3 ≥ 1–2 m away from the modem, in a high, open spot, facing the PC. ⇒ **EXP-008**.

## EXP-008 — Turn off the old modem's Wi‑Fi + move the BE3 > 1 m away from the modem

- **When:** before 13:54 (+07), done by the user; antennas **not yet** tilted.
- **Scan at 13:54:** OldModemNet, CNBN and the hidden SSID `c6:2c:7b:d8:ea:99` **have disappeared** ⇒ confirms they were broadcast by the modem. **NeighborNet** (`c4:2c:7b:d4:ea:9a`, channel 36, 63%) **is still there** ⇒ either the modem's 5G radio is not fully off, or NeighborNet is a different device.
- **New BE3 SSIDs appeared:** `HomeNet_5G` (02:5e:00:9a:40:24, ch157), `HomeNet_Wi-Fi5` (…:40:31, ch11), `HomeNet_5G_Wi-Fi5` (…:40:32, ch157) ⇒ the user has split the bands and/or enabled the Wi‑Fi 5 compatibility network on the BE3 (needs confirmation). The PC is currently on `HomeNet_5G`.
- **Signal worse after moving:** **−78 … −80 dBm (39–45%)**, compared with −76 before moving and −66 this morning. Rx still 6 Mbps in 8/10 minutes (13:40–13:50).
- **Incidents:** router_down 154s (13:49), 152s (13:51), and 3 times 5s (13:52–13:54); the PC roamed via 2.4G ch11 and then back to 5G.
- Logger restarted at 13:45:50 (PID 19984) without a preceding `logger_stop` ⇒ lost data for 13:43–13:44.
- **Assessment:** the modem interference cleanup is done, but **the new location has weakened the signal to the PC** ⇒ right now weak signal (≤ −78 dBm) is the dominant factor. Need to reposition the BE3 at a spot with line of sight/fewer walls to the PC (still ≥ 1 m from the modem, extending the WAN cable if needed), adjust the antennas, and/or enable 穿墙 mode.

## EXP-009 — Disable MLO on the BE3 (done by the user)

- **When:** around 13:52–14:01 (+07), the user needs to confirm the exact time.
- **User's remark:** "with MLO off, the connection no longer drops like before".
- **User confirmed the order:** (1) disable MLO → (2) change the 5G channel to **48** → (3) enable **Wi‑Fi 5** mode (compatibility network) on the router.
- **Accompanying changes, observed in the same time window** (⇒ **the impact of MLO cannot be isolated**):
  - The BE3 split SSIDs by band: `HomeNet_5G` and the Wi‑Fi 5 compatibility SSIDs `HomeNet_Wi-Fi5` (2.4G ch11), `HomeNet_5G_Wi-Fi5`.
  - The PC is connected to `HomeNet_5G_Wi-Fi5` ⇒ **802.11ac standard** (no more ax/be) — equivalent to a "force Wi‑Fi 5" experiment on the router side.
  - 5G channel changed from **157 → 48** (14:02).
  - Signal improved: −78/−80 dBm → **−73/−74 dBm (60%)** — possibly due to the lower channel or further adjustments to the router/antennas.
- **Per-minute data (router):** 13:57–14:01 still lost 8–30 packets/minute while switching SSID; **14:02: 0/60 packet loss, avg 6 ms, max 32 ms**, Rx 234 Mbps.
- **Results 14:02 → 14:21 (19 minutes):** router lost **3.1%** (35/1124) compared with **34.9%** in the 11:30–13:14 window and 19.7% on channel 157; only **1** router connection loss (3s); **0** Windows events (8003/10002/4003). RSSI −72…−76. Rx showed 6 Mbps in many minutes but without accompanying packet loss ⇒ netsh's Rx rate figure (rate of the most recent frame) is unreliable when traffic is low — **do not use it on its own as an incident indicator**.
- Internet (1.1.1.1 / 8.8.8.8) still lost 13–15% while the router only lost 3% ⇒ a separate problem on the WAN/ISP side or ICMP rate limiting — keep monitoring, separate from the Wi‑Fi problem.
- **Router information (路由器信息 page):** it is actually a **华为路由 BE3 Pro 雷电版** (BE3 Pro, "Thunder" edition), software 6.1.0.11 (V6R1), HarmonyOS 6.1.0, hardware VER.A; uptime 40 minutes at ~14:20 ⇒ **the router restarted at ~13:40** when the changes were applied. WAN IP 203.0.113.10, ISP DNS 123.23.23.23 / 123.26.26.26 (VNPT). Wi‑Fi 定时 (Wi‑Fi off schedule): empty. Wi‑Fi 中继 (repeater): off. The channel page notes: channels below 149 are newly opened channels in China, some older devices may not be able to connect.
- **Preliminary assessment (14:04):** right direction, consistent with the "Wi‑Fi 7 ↔ MediaTek MT7922 compatibility" hypothesis (MLO is a Wi‑Fi 7 feature the card does not support). But there are only ~2 minutes of clean data ⇒ **no conclusion yet**. Evaluation milestones: 15:00 and end of day.

### EXP-009 continued: 14:21 → 14:41 (still 5G ch48, SSID `HomeNet_5G_Wi-Fi5`, 802.11ac)

- Router lost **0–7 packets/minute** (~5%), short 3–4s connection losses roughly every 5 minutes, no long incidents. RSSI gradually improved −76 → −70 dBm. ⇒ **Best window of the day**, but not yet perfect.

## EXP-010 — Move the PC to 2.4GHz ("trade speed for stability")

- **When:** 14:42 PC moved to `HomeNet_Wi-Fi5` (2.4G, ch11); around 15:20 moved to `HomeNet` (2.4G ch11, 802.11ax). Logger restarted at 15:20:32 (suspected PC restart/re-sign-in).
- **Result: clearly worse than 5G ch48:**
  - 14:42–14:51: 1–10 packets/minute (equivalent to 5G).
  - **14:52–15:17:** 9–36 packets/minute (**~30%**), average ping 70–180 ms, Rx/Tx dropped to **1 Mbps** (the lowest 2.4G rate), RSSI at times −90…−93.
  - **15:27–15:37: total loss for ~10.7 minutes** (router_down 641s) while Wi‑Fi was still `connected` ⇒ prolonged silent hang; 15:39–15:41 lost another 150s.
- **Possible causes:** 2.4G channel 11 is crowded (many neighbor networks on channels 4/7/11, utilization 35%), the 2.4G band is prone to interference (microwave ovens, Bluetooth, wireless devices); the MediaTek driver still hangs.
- **Conclusion:** 2.4G is **not** more stable ⇒ go back to 5G ch48 (`HomeNet_5G_Wi-Fi5`), the best configuration measured so far.
- **Overall assessment after EXP-001 → EXP-010:** the router-side changes (turning off the modem's Wi‑Fi, disabling MLO, channel 48, Wi‑Fi 5 mode) reduced packet loss from ~35% to ~3–5%, but **periodic short hangs remain** on every band/channel ⇒ the remainder is most likely due to the **MediaTek card/driver + a signal of only −70…−76 dBm**. Most valuable next step: **EXP-005 (wired connection)** or **switching to an Intel card**.

## EXP-013 — Disable Wi‑Fi 7 on the router, PC back on `HomeNet_5G` (Wi‑Fi 6, channel 40)

- **When:** ~15:43 (+07). User: "turned off Wi‑Fi 7 and switched back to 5G". PC: `HomeNet_5G`, **802.11ax**, **channel 40**, RSSI **−67…−68 dBm** (better than the −72…−76 of ch48).
- **Results 15:43 → 16:22 (39 minutes):** router lost **12.7%**, **31** connection losses (total 249s), 29/39 minutes had packet loss; Windows only 1 disconnect. Incidents clustered around 16:08–16:12; from 16:13 → 16:22 no incidents.
- **Comparison:** better signal but **higher** packet loss than the `HomeNet_5G_Wi-Fi5` ch48 window (802.11ac, ~3–5%). ⇒ Suggests: the MediaTek card is more stable in **Wi‑Fi 5 (ac) mode** than in Wi‑Fi 6 (ax) — consistent with the EXP-004 direction (`wifi_mode_ac`). Not certain, since the channel also changed (48 → 40) and the observation time was short.
- **Signal reference before installing the window antenna:** −67 dBm.

## EXP-004 — Force the card to run Wi‑Fi 5 (`wifi_mode_ac`)

- **When:** ~16:26 (+07), with the user's consent; ran [scripts/manual/set-phymode.ps1](../scripts/manual/set-phymode.ps1) `-Mode ac` (Admin). Log: `data/manual/phymode-*.log`.
- **Change:** driver `802.11ax/ac/n/abg`: **1. 802.11ax → 2. 802.11ac**. The card restarted itself and reconnected immediately.
- **Kept unchanged (only one factor changed compared with EXP-013):** SSID `HomeNet_5G`, channel 40, router in the mode with Wi‑Fi 7 / MLO disabled, EXP-001 still applied.
- **Right after the change:** Radio type **802.11ac**, RSSI −69 dBm.
- **Compared with:** EXP-013 (same SSID, same channel, 802.11ax): router lost 12.7%, 31 connection losses / 39 minutes.
- **Restore:** `scripts\manual\set-phymode.ps1 -Mode ax` (Admin).

**Observations:**
- **16:27 → 17:26 (59 minutes):** router lost **17.7%** (594/3352), **57** connection losses (593s), 42/59 minutes had packet loss; Windows 0 disconnects (all silent hangs). RSSI dropped to **−73 dBm (48%)** at 17:26 (from −67…−69).
- **Conclusion:** forcing 802.11ac **did not improve** things, it was even worse than EXP-013 (12.7%) — although the signal also weakened, so the comparison is not entirely fair. The hypothesis "the card is more stable on Wi‑Fi 5" is **not confirmed**; the good window at 14:02–14:41 was most likely due to the conditions at that time (signal / interference by time of day), not the Wi‑Fi standard.
- **17:29:50 — restored** to `1. 802.11ax` (`set-phymode.ps1 -Mode ax`, with the user's consent). PC reconnected to `HomeNet_5G` ch40, 802.11ax, −72 dBm.
- **Assessment:** after 10+ configuration changes during the day, packet loss fluctuated between 3–35% by time of day regardless of configuration ⇒ **configuration tuning has run out of effect**; the bottleneck is the **physical path** (through the floor + desktop, antennas behind the PC case) and/or the MediaTek card ⇒ EXP-011 (window antenna) / EXP-012 (AX3 + cable) is the right next step.

## EXP-014 — Rotate the PC case so the card's antennas face outward

- **When:** sometime between 17:32 → 18:44 (+07). The logger has no data for this period (suspected PC shutdown/restart while rotating the case; the logger restarted itself, PID 12676). The extension antenna is **not yet** in use.
- **Before:** antennas at the back of the PC case, facing into the space under the desk, right next to the metal desk frame and a bundle of cables (photo sent by the user).
- **Kept unchanged:** `HomeNet_5G`, channel 40, 802.11ax (restored 17:29), router: modem Wi‑Fi off, MLO off, Wi‑Fi 7 off.
- **Results 18:44 → 19:08 (25 minutes):**

  | Metric | 17:20–17:31 (before) | **18:44–19:08 (after)** |
  |---|---|---|
  | Router packet loss | 2–32 packets/minute, ~25% | **3 / 1475 packets (~0.2%)** |
  | Average router ping | 5–200 ms | **3–5 ms** |
  | Rx rate | usually 6–27 Mbps | **459–600 Mbps stable** |
  | RSSI | −71…−76 dBm | −67…−74 dBm |

- **Assessment:** RSSI only improved slightly, but Rx is stable at a high level and packet loss is almost gone ⇒ previously **one or both antennas (2×2 MIMO) were blocked by the metal desk frame / PC case**, causing rate collapse and hangs. This is the biggest improvement of the day. More time (overnight, peak hours) is needed to confirm.
- The extension antenna (EXP-011) and the AX3 (EXP-012) become **conditional**: only needed if the incidents come back.

### Check at 23:07 (after EXP-014)

- **Data 18:44 → 20:40:** router lost **0.81%** (55/6793), total connection loss 14s. RSSI 5G ch40: −63…−67 dBm, rate 1201 Mbps.
- **Data gap 20:41 → 23:07 (2.5 hours):** the logger was not running. Windows logged multiple OS stop/start events (Kernel-General 12/13) at 19:48, 20:57, 20:58, 21:28 and sleep/resume at 17:32, 21:32 — suspected that the PC restarted/slept several times (cause unclear; possibly due to the user's handling around the PC case).
- **The Startup shortcut `StableInternet ping-logger.lnk` has disappeared** (the folder only contains `Hermes_Gateway.vbs`) — unclear who deleted it ⇒ the logger did not restart itself after the PC booted. **Reinstalled at 23:08** and started the logger (PID 22028).
- **Logger shortcoming:** the "already running" check via `logger.pid` can be wrong when the old PID is reused after a restart ⇒ needs fixing (match the process name/command line). Noted as a to-do for when the real tool is written.
- 23:07: PC on `HomeNet` 2.4G ch11 (−69 dBm) → the user deliberately switched back to `HomeNet_5G` ch40 (−64 dBm, 802.11ax, 1201 Mbps).

### Overnight results for EXP-014: 2026-10-03 23:08 → 2026-10-04 08:41 (9.6 hours, 573 minutes)

| Target | Packet loss | Avg | Jitter |
|---|---|---|---|
| Router | **0.39%** (133/33972) | 5.0 ms | 3.6 ms |
| 1.1.1.1 | 8.08% | 44.5 ms | 3.8 ms |
| 8.8.8.8 | 9.16% | 51.6 ms | 3.6 ms |

- **Only 1 router connection loss (3s)**; Windows: 0 disconnects, 0 IHV 10002 events, 0 limited connectivity. Compared with the baseline of the previous 7 days: ~5.4 disconnects/24h and 4.1 IHV crashes/24h.
- **The Wi‑Fi part is now stable.** Packet loss of ~8–9% to the Internet but only 0.39% to the router, uniform throughout the night (572/573 minutes) and low jitter ⇒ suspect **ICMP rate-limited/deprioritized on the ISP side or the router's WAN**, not Wi‑Fi. Needs checking via TCP/DNS (see the logger improvements section).
- ⚠️ **Update 2026-10-04 (SIC-36): the suspicion above is not yet supported.** The Python monitor (parallel ping, one thread per target) measured Internet packet loss of only **0.3%** in the first 35 minutes, and in the parallel measurement at 09:12 the PowerShell logger reported 8/59 lost on 1.1.1.1 while Python measured 0/40 at the same time. The 8–9% figure may be a **measurement error of the PowerShell logger** (sequential pings, delays when calling `netsh`) rather than the ISP limiting ICMP. No conclusion yet: only daytime data so far. How to tell them apart: compare `cloudflare`/`google` (ping) with `tcp_*`/`http_*` (probe) in `metrics.db` after one night; diagnostic #5 already distinguishes these two cases.
- ✅ **Resolved 2026-10-04 15:11 (SIC-36): the ICMP packet loss is real, but only ICMP is lost, real connections are fine.** `metrics.db` data 10:40 → 15:11 (4.5 hours, 265 minutes, Python monitor):

  | Target | Sent | Lost | Rate |
  |---|---|---|---|
  | ping `cloudflare` 1.1.1.1 | 15,426 | 879 | **5.70%** |
  | ping `google` 8.8.8.8 | 15,426 | 1,183 | **7.67%** |
  | ping `router` | 15,426 | 123 | 0.80% |
  | `tcp_cloudflare` 1.1.1.1:443 | 1,549 | 1 | 0.06% |
  | `tcp_google` 8.8.8.8:443 | 1,549 | 2 | 0.13% |
  | `http_cloudflare` generate_204 | 1,549 | 1 | 0.06% |

  - 91/265 minutes had ICMP loss, only 2 minutes had probe failures; **0 minutes** in which all Internet pings were completely lost and **0** `internet_down` events generated from ICMP-only loss. In the worst hour (13h) ICMP lost 19% while TCP lost 0.28%.
  - Losses come in **bursts** (one minute can lose up to 27/58 packets), 1.1.1.1 and 8.8.8.8 lose packets **in the same minutes** (86/89 minutes), in 53 of which the router also lost a few packets ⇒ not each DNS provider rate-limiting separately; the ICMP bottleneck lies on the shared path (router/WAN/ISP), not yet precisely identified. It does not affect TCP/HTTP, so it is not an incident the user can perceive.
  - So the "PowerShell logger measurement error" assessment above is **wrong**: the Python monitor also measured 5–8%. The initial 0.3% figure was because the loss bursts had not yet occurred during those 35 minutes.
  - Side note: the TCP handshake time to 1.1.1.1:443 (median 105 ms) is twice the ping (47 ms), whereas for 8.8.8.8 they are nearly equal (66 vs. 54 ms). Cloudflare's Anycast may route TCP differently. Therefore probes are only used to know "is the Internet still up or not", not as a latency measurement.
- **Conclusion:** the main cause of the dropouts was **the card's antennas being blocked by the metal desk frame/PC case** + the configuration factors already dealt with (modem broadcasting Wi‑Fi, MLO). **No need to buy the AX3 yet.** EXP-011/012 become fallbacks.

## EXP-015 — `upload_shaping` on the dev PC, through the tool (2026-10-07)

- **What:** the first run of the upload limit (ADR-0016) on a real machine, from an Administrator PowerShell, with the owner's agreement: plan, `enable --apply`, `disable --apply`, then the three QoS commands of `app/winsys.py` on a throwaway policy `StableInternet-Test` at 900 and 800 Mbps (above the line's speed, so traffic was not limited).
- **Before:** no QoS policy in the default store or in the `ActiveStore`.
- **Enable:** measured the upload (~15 s), then **refused**: "Latency rises only 25 ms under upload; nothing to fix, so nothing was changed" (threshold 30 ms). No backup written, no policy created; `disable` answered "Already off". This is the intended behavior on a line without upload bufferbloat, so the limit itself, and the measurement taken again after it, could not be exercised on this line today.
- **QoS commands, checked on the real machine:**

  | Step | Read back (`Get-NetQosPolicy`) |
  |---|---|
  | `qos_policy_set` (no policy yet ⇒ `New-NetQosPolicy -Default`) | 900000000 bit/s |
  | `qos_policy_set` again (exists ⇒ `Set-NetQosPolicy`) | 800000000 bit/s, exactly |
  | Both stores | default store: `StableInternet-Test`, 800000000; `ActiveStore`: `stableinternet-test` (lower case), 800000000 — active at once |
  | `qos_policy_remove` | gone from both stores; a second remove is a no-op |

- **After:** no QoS policy in either store; the machine is as before.
- **Takeaways:** the rate Windows stores is exactly the rate asked for (no rounding at these values); a policy created in the default store shows up in the `ActiveStore` immediately, under a lower-case name (`-eq` in PowerShell ignores case, so the removal still finds it). Whether the limit lowers latency under upload remains to be measured on a line that has upload bufferbloat — the tweak records that itself (`tweak_verified`).

## EXP-011 — Wired extension Wi‑Fi antenna for the PCIe card (planned)

- **Context:** the PC is **upstairs**, the BE3 router is **downstairs**, a LAN cable cannot be run ⇒ explains the signal of only −66…−80 dBm.
- **Ordered:** a Wi‑Fi antenna with a separate base and an extension cable, attached to the TP-Link card (MT7922).
- **Requirements when buying/installing:** **RP-SMA** connector (the TP-Link card's standard); **2 antennas** (2×2 card); **2.4 + 5 GHz** bands; **1–1.5 m** cable (thin cable loses ~1–2 dB/m at 5 GHz, should not be too long); place the base high, away from the PC case/monitor/metal.
- **Antenna orientation when the router is directly on the floor below:** vertical omnidirectional antennas have a "blind spot" directly above/below ⇒ tilt the antennas ~45° or lay them horizontally at both ends (the PC and one of the BE3's antennas).
- **Expectation:** a few dB up to ~10 dB signal improvement if the antennas currently sit behind the PC case near the floor. Does **not** fix the MediaTek driver hang.
- **Measurement:** compare RSSI and packet loss to the router before/after, on the same SSID `HomeNet_5G_Wi-Fi5` ch48.

## EXP-012 — Final solution: add a Huawei WiFi AX3 upstairs, cable to the PC (planned)

- **User's decision (2026-10-03):** buy an additional **Huawei WiFi AX3**, pair it with the BE3 Pro (HarmonyOS Mesh+), place the AX3 upstairs, **PC wired via LAN to the AX3** ⇒ completely removes the MediaTek card from the path.
- **Target diagram:**
  ```
  Modem (bridge, Wi‑Fi off) ── BE3 Pro (main router, downstairs) ~~ backhaul 5G ~~ AX3 (node Mesh+, upstairs) ── LAN ── PC
  ```
- **Notes when buying/installing:**
  - The BE3 Pro is a **Chinese domestic version** ⇒ prefer an AX3 that is **also the domestic version** to be sure Mesh+ pairing works (mixing international and domestic versions may fail to pair — not verified). Some users report the AX3 pairs in mesh with the BE3.
  - Distinguish the AX3 variants (AX3 / AX3 Pro / AX3 New…); choose one with Mesh+ / 鸿蒙组网.
  - Pairing: place the AX3 near the BE3 for the first pairing (H button / 智慧生活 app), then take it upstairs.
  - AX3 location: where it **receives the BE3 signal best upstairs** (top of the stairs, directly above the router), target ≥ −65 dBm at the AX3; it does not have to be next to the PC since it is wired.
  - **Update:** the study's window is an **indoor window looking straight down into the living room**, where the BE3 is placed ⇒ there is a **line of sight** BE3 ↔ window, without going through concrete. The current path to the PC must go through **1 floor + 1 desktop**. ⇒ **Recommended location: the AX3 right at the window, inside the room**, antennas oriented perpendicular to the straight line to the BE3; place the BE3 in an open spot with a view of the window. Avoid metal window frames/bars right in front of the antennas; with film-coated/low‑E glass, keep the window slightly open.
  - The user is considering: (a) placing it **on the floor** of the study, or (b) **hanging it at the window**, with the antennas sticking outside. Recommendation: **measure with a phone** at the candidate locations (the floor directly above the BE3, the top of the stairs, the window) and pick the strongest spot. The floor is only good when **the BE3 is almost directly below** (and the BE3 should be placed high, near the ceiling of the floor below). The window is only worth trying when the BE3 is also next to a window on the same side of the house; avoid leaving the device outdoors (sun, rain, heat).
  - "组网信道模式": with wireless mesh, set it to **同信道部署** (same channel) so the backhaul works; keep the 5G channel fixed (48) or let the router choose automatically once the mesh is stable.
  - MLO stays **off** (already seen to be linked to the incidents); the Wi‑Fi 5 compatibility network can be turned off once the PC is wired, if no other device needs it.
- **Measurement:** the logger follows the gateway automatically; expect router packet loss ≈ 0%, no more silent hangs. Once stable, the **MediaTek Wi‑Fi card can be disabled** (or kept as a fallback) and restoring EXP-001 can be considered.
- **Extension antenna (EXP-011):** becomes a fallback option; no longer needed if EXP-012 succeeds.

## Finding: things got worse after replacing the router (13:30)

The user reports dropping **more often than before replacing the router**. History of event 8001 (Windows log since 2026-09-08):

| Network | Number of connections | From | To |
|---|---|---|---|
| OldModemNet 5G | 53 | 09-08 | 10-02 13:58 |
| OldModemNet (2.4G) | 7 | 09-21 | 10-02 13:59 |
| CNBN | 4 | 10-02 13:47 | 10-02 13:54 |
| NeighborNet | 8 | 10-02 13:57 | 10-02 15:35 |
| **HomeNet (BE3)** | 10 | **10-02 15:26** | now |

Disconnects (8003) per day: 09-08: 1 · 09-10: 8 · 09-11: 1 · 09-21: 5 · 09-26: 3 · 09-27: 3 · 09-28: 1 · 10-01: 6 · **10-02: 36** (mostly from switching back and forth between 4 networks while installing the new router) · 10-03: 2.

⇒ **The BE3 was put into use on the afternoon of 2026-10-02.** Before that the PC used "OldModemNet 5G". The OldModemNet / CNBN / NeighborNet networks share the MAC family `c4/c6:2c:7b:…` (same device/vendor) and **NeighborNet + a hidden SSID are on channel 36** — exactly the channel the BE3 picked automatically.

Hypotheses for why it is worse than the old router (ordered by likelihood):
1. **Wi‑Fi 7 (BE3) ↔ MediaTek MT7922 card (Wi‑Fi 6E) compatibility**: Rx collapsing to 6 Mbps while the signal is still good is a typical sign of a rate-control / new-feature bug (11be, OFDMA, TWT…) between router and driver. ⇒ try disabling `be` on the BE3's 5G (EXP-003b).
2. **Co-channel interference on channel 36** with the old device still broadcasting nearby ⇒ being tested with EXP-002 (channel 157).
3. **BE3 location** farther/more obstructed than the old transmitter (RSSI −66 → −76 dBm).
4. EXP-001 (disabling power saving) — not ruled out.

Note: Windows does not log the "silent hangs", so there is no equivalent comparison data for the old-router period.

## BE3 router Wi‑Fi configuration (screenshot of the admin page, 2026-10-03 ~11:50, firmware with Chinese UI)

| Setting | 2.4G | 5G |
|---|---|---|
| Channel (信道) | 自适应 — Auto | 自适应 — Auto (actually on **channel 36**) |
| Standard (模式) | 802.11b/g/n/ax/be | 802.11a/n/ac/ax/be |
| Bandwidth (频宽) | 20/40 MHz | 20/40/80 MHz (160MHz not used ⇒ no DFS) |
| 11be guard interval (前导间隔) | 短间隔 — short | 短间隔 — short |
| Hide SSID (隐身) | Off | Off |
| WMM | On | On |

Firmware note: to choose a channel manually you must turn off "自动优化信道" (automatic channel optimization) and set "组网信道模式" (channel mode when networking/pairing) to 同信道 (same channel) / 异信道 (different channel). There is a "一键优化信道" button (one-tap channel optimization). No transmit power / wall-penetration setting seen on this page yet.

## Planned experiments

| ID | Description | Condition for running |
|---|---|---|
| EXP-002 | Change the 5GHz channel on the router (36 → the 149–161 group) | **Priority raised** (11:20): connection losses coincide with Rx rate collapse despite good signal ⇒ suspected channel interference |
| EXP-001b | A/B: restore EXP-001 for 1–2 hours, logger still running | To get a baseline with the same yardstick, determine whether EXP-001 is better or worse |
| EXP-003b | Disable Wi‑Fi 7 (`be`) on the BE3's 5G: 模式 → 802.11a/n/ac/ax | **High priority** if channel 157 does not improve things — suspected Wi‑Fi 7 ↔ MediaTek compatibility |
| EXP-003 | Roll back the MediaTek driver 3.6.0.1434 → 25.40.2.585 (`oem18.inf`) | If EXP-001 does not reduce event 10002 |
| EXP-004 | `wifi_mode_ac` (force 802.11ac) | If still crashing after EXP-003 |
| EXP-005a | **iGate 302S (unused) placed near the PC in repeater/wireless client mode of the BE3, cable iGate → PC** (zero cost) | First check whether the iGate has a Repeater/Client mode or only an EasyMesh agent (requires the VNPT controller). Removes the MediaTek driver; the wireless iGate↔BE3 hop remains |
| EXP-005 | **Temporary wired connection PC ↔ BE3** (Intel I219-V), a few hours, logger still running | **Should be done soon** — a discriminating test: incidents gone ⇒ fault is in the Wi‑Fi/card hop (mesh + cable will fix it); incidents remain ⇒ fault is in the BE3/modem/ISP (mesh won't help) |
| EXP-006 | Mesh: a second node next to the PC, cable node → PC | After EXP-005 if the fault is confirmed to be in the Wi‑Fi hop. The node must run in **AP/bridge mode** (or Huawei Mesh+ mesh), not router mode (avoid double NAT) |
