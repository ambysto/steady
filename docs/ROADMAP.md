# Roadmap

## Phase 0 — Design *(in progress)*
- [x] Measure the current state of the development machine → [BASELINE.md](BASELINE.md)
- [x] Initial set of design documents
- [ ] Settle the open questions → [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md)
- [ ] Settle the UI direction → [UI-DESIGN.md](UI-DESIGN.md)

## Phase 1 — Monitoring (read-only)
- [x] `icmp`, `dnsprobe`, `winutil`
- [x] `monitor` + `storage` (per-minute statistics, events)
- [x] Prevent two instances running at once; autostart via Task Scheduler (installed on the development machine 2026-10-04, monitor running since 09:59)
- [ ] Server + Overview and Log tabs
- [ ] Run for 1–2 days to collect "before optimization" data

## Phase 2 — Diagnostics
- [x] All checks in [DIAGNOSTICS.md](DIAGNOSTICS.md) (`python -m app.diagnostics`; check #10 waits for the tweak framework)
- [x] Save the results of diagnostic runs for comparison (`--compare`)

## Phase 3 — Optimization (toggles)
- [ ] Tweak framework + backup/restore
- [ ] `low` risk tweaks
- [ ] `medium` / `experimental` tweaks
- [ ] Mark when tweaks are turned on/off on the chart to compare before/after

## Phase 4 — Watchdog & utilities
- [ ] Watchdog per [WATCHDOG.md](WATCHDOG.md)
- [ ] Start with Windows
- [ ] Notifications (toast) on connection loss / watchdog intervention

## Ideas for later
- Failover to a backup path (USB 4G, phone tethering, a second network)
- Weekly report (uptime, number of drops, time offline)
- Slowdown history and weekly/monthly/quarterly ISP reports (Pro) — [ADR-0021](adr/0021-slowdown-history-and-isp-reports.md); includes the CSV export for the ISP
