# Open questions

Points to discuss and settle **before writing code**. When settled, record the decision here and (if it is an architectural decision) create an ADR in `adr/`.

| # | Question | Options | Proposal | Status |
|---|---|---|---|---|
| Q1 | "macOS native" UI direction | A. macOS style on Windows · B. A real macOS app | A | ✅ **A** (2026-10-03) — [ADR-0002](adr/0002-macos-style-ui-pywebview.md) |
| Q2 | Application shell | Browser tab · pywebview · Tauri · Electron | pywebview | ✅ **pywebview** (2026-10-03) |
| Q3 | Dependency policy | Standard library only · allow a few small libraries (pywebview, pystray) | Allow, if Q2 = pywebview is chosen | ✅ **Allow small libraries** (2026-10-03) |
| Q4 | System tray icon + status popover | Yes · No | Yes | ⚪ Not settled |
| Q5 | Window button style (if frameless) | Traffic lights on the left · Windows buttons on the right | — | ✅ ~~Mac style – traffic lights on the left~~ (2026-10-03) → **changed 2026-10-04: native Windows window frame**, buttons on the right. A frameless window gets no Snap Layouts, no dragging to an edge to split the screen and no Win+arrow, because Windows does not know it is a window drag. The title bar is painted with the same color as `--window` (DWMWA_CAPTION_COLOR). |
| Q6 | Backup path (failover) | None · USB 4G · phone tethering · a second wired network | Identify the devices available | ⚪ Not settled |
| Q7 | Scope | This PC only · several PCs in the home · also manage the router | This PC only (initial phase) | ⚪ Not settled |
| Q8 | Driver management in the tool | Display/guidance only · automatic rollback | Display only | ⚪ Not settled |
| Q9 | UI language | Vietnamese · English · Bilingual | Selectable, English by default | ✅ Settled 2026-10-04 — [ADR-0006](adr/0006-i18n.md) |
| Q10 | Watchdog default | Off · On | Off, the user turns it on | ⚪ Not settled |
| Q11 | Can a LAN cable be run to the PC? | — | — | ✅ **No** (2026-10-03): the PC is upstairs, the router is downstairs. The user ordered a Wi‑Fi antenna with an extension cable (EXP-011). Alternatives: Powerline, a Huawei repeater/mesh upstairs + cable to the PC, a flat LAN cable along the stairs |
| Q12 | Which mesh device? | Huawei router with Mesh+ support · another brand's mesh system in AP mode · replace with an Intel Wi‑Fi card | Wait for the results of EXP-005 | ✅ **Huawei WiFi AX3** placed upstairs, paired via Mesh+ with the BE3 Pro, cable AX3 → PC (2026-10-03) — EXP-012 |

## Discussion notes

_Add points to discuss here._
