# ADR-0020: One "Check → Fix → Result" flow on the Overview, and a value card from the log

- **Status:** Accepted
- **Date:** 2026-10-07
- **Related:** [ADR-0005](0005-unelevated-server-uac-writes.md), [ADR-0007](0007-measured-impact.md), [ADR-0016](0016-measured-tweaks.md), [DIAGNOSTICS.md](../DIAGNOSTICS.md), [TWEAKS.md](../TWEAKS.md), SIC-98

## Context

The app does real work: 15 regular diagnostics checks (plus the on-demand bufferbloat check), tweaks that are backed up, verified and restorable, measured tweaks that prove their effect, a watchdog that recovers the connection on its own, and before/after comparisons for manual steps (ADR-0007). Users do not feel much of it. Diagnostics and Optimize are separate pages, so the user has to connect a warning on one with the switch that fixes it on the other. The watchdog does its best work while nobody is looking, and its recoveries only show up as lines in the Log.

Consumer "optimizer" apps do much less, yet users find them useful because of how they present the work: one Scan button, visible progress, "N issues found", one Optimize button, a result screen. Network Booster (Microsoft Store) only compares DNS lookup times and switches DNS. The presentation is worth learning. The exaggeration that often comes with it (inflated issue counts, alarming health scores, "optimized!" with nothing measured) is not. It is also what gets an app flagged as potentially unwanted software, which matters for a signed app sold through the Store.

The product's motto is **practical, useful, honest**. Every number the flow shows comes from a measurement or from the log, and "nothing to fix" is a good result, not an empty screen.

The pieces already exist: `diagnostics.run_all`, `suggestions.suggest` (tweaks and manual steps from a run, most serious first), the tweak manager, `tweak_verified` events, `impact.compare`. Elevated operations take **one** tweak per UAC prompt (`tweak-enable <id>`), so fixing three things today means three prompts.

## Decision

1. **One primary action on the Overview: "Check my connection".** It runs the regular checks (`diagnostics.CHECKS`, the same run the Diagnostics page starts and stores). On-demand checks that load the line (bufferbloat #14) are not part of it. They stay on the Diagnostics page, where their cost is explained. Once the latest result is older than 3 hours it may no longer hold, so "Check again" takes the centre as the big button and the old result stays below it, marked with its age.
2. **Progress is real.** The server reports each check as it starts and finishes, and the UI lists them as they run ("Wi‑Fi signal… DNS… drops in the last 24 hours…"). There is no minimum duration and no animation that runs ahead of the work.
3. **What counts as a problem.** The headline counts the stored results whose status is `warn` or `bad`. `info` and `ok` are never counted. Each counted problem gets one plain sentence and what can be done about it, in one of three forms:
   - *The app can fix it*: a tweak suggested by `suggestions.suggest`.
   - *You can fix it*: a manual step, with "I did this" as today.
   - *Nothing on this PC fixes it*: for example a route detour at the ISP. The advice is still shown.

   The Fix button names how many it covers ("Fix 2 of 3"), so the count of problems and the count of fixes are never mixed up.
4. **"Nothing to fix" is a full result.** It shows the numbers behind it (latency to the router and to the Internet, packet loss, drops in the last 24 hours) and when the check ran.
5. **The Fix button only takes low-risk tweaks.** These are the tweaks with `risk == "low"` that the run suggests, that are supported, and that are not already on. Medium and experimental tweaks, and measured tweaks (ADR-0016, which take a ~15 s measurement and may refuse), are never part of the batch. They stay as separate actions with their own confirmation sheet. Before the UAC prompt, the user is told when the network will drop for a few seconds (🔌 tweaks).
6. **One UAC prompt for the batch: new elevated operation `tweak-enable-many`.** Its value is a comma-separated list of tweak ids and nothing else. The helper does not trust the list. It drops ids that are not in the catalog and refuses any tweak that is not low-risk. It enables each one through the tweak manager as `tweak-enable` does, with the same backup, verification and events. Tweaks that do not drop the network run first. One failure does not stop the others. The result lists each id as changed, already on, not supported or failed, with its message. This adds no new power to the helper, which can already enable each of these ids on its own; it only removes the extra prompts.
7. **The result separates "turned on" from "improved".** After fixing, the checks that called for the fixed tweaks run again, and the result shows each problem before and after. A check that only reports whether a tweak is on (#10) turning `ok` is not shown as evidence of improvement. Most tweaks act on drops over hours or days, so their effect is reported later through the before/after comparison of ADR-0007, and the result screen says when to expect it. When a re-run check is no worse but no better, the result says so plainly and offers to undo the batch.
8. **A value card on the Overview, counted from the log.** Over the last 7 days it shows:
   - how many times the watchdog recovered the connection: a `watchdog_recovered` event with the message `watchdog.event.recovered_after` (the problem cleared within the verify window of an action) whose action was real, a `watchdog_action` event and not a `watchdog_dry_run`. The watchdog also writes `watchdog_recovered` in dry-run mode, where nothing was done, and with `watchdog.event.recovered` when the connection came back after the verify window, which may be on its own; neither is counted;
   - measured tweaks that helped (`tweak_verified` with *helped*);
   - manual steps whose before/after comparison came out better (ADR-0007);
   - switches to the backup path (`failover_switched`).

   Each line opens the Log filtered to those events. When there is nothing to count, the card says how long the app has been watching and that nothing needed fixing. It never shows an empty zero. It never estimates "time saved" or "speed gained", because the app does not measure those.
9. **Things this flow never does:** a health score or percentage, red alarms when nothing is `warn` or `bad`, counting `info` results or anything that does not affect the connection, artificial delays, notifications nagging the user to scan again, and "optimized" claims without a measurement behind them.
10. All new text goes through `app/locales` (ADR-0006). The Diagnostics and Optimize pages stay as the detailed views, and the flow links into them.

## Consequences

- ✅ The user sees what the app found, what it fixed and what it has done since, in one place, with numbers that hold up.
- ✅ Fixing the usual problems takes one click and one UAC prompt instead of a trip through two pages and one prompt per tweak.
- ✅ The quiet work (watchdog recoveries, tweaks that proved themselves) becomes visible without inventing anything.
- ⚠️ The flow is only as good as `suggestions.suggest`. A check that warns without a matching tweak or manual step shows up as "nothing on this PC fixes it" until one is added.
- ⚠️ The result right after fixing is often "turned on, effect measured over the coming days", which is less satisfying than an instant "improved". That is the honest answer for tweaks that act on drops. The value card and the ADR-0007 comparison carry the proof later.
- ⚠️ `tweak-enable-many` is a new elevated entry point. It must validate every id in the helper, like `tweak-enable`, and needs its own tests (unknown id, non-low-risk id, empty list, one tweak failing among several).
- ⚠️ A batch with several 🔌 tweaks can drop Wi‑Fi more than once while it runs. Ordering them last keeps the earlier steps unaffected, and the user is warned once before the prompt.
- Work items: SIC-99 (this ADR), SIC-100 (mockup), SIC-101 (check flow and progress), SIC-102 (Fix and the elevated batch), SIC-103 (value card).
