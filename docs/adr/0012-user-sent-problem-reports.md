# ADR-0012: Problem reports are written by the app, read in full and sent by the user

- **Status:** Accepted
- **Date:** 2026-10-05
- **Related:** [PRIVACY.md](../../PRIVACY.md), [ADR-0011](0011-apple-measurement-history.md), SIC-60

## Context

We want to hear about bugs and bad verdicts from people using the app, with enough facts to act on. The privacy policy promises no telemetry, no analytics, no third-party code, and that nothing is sent anywhere. Options considered:

| | Sends automatically | Third-party code | Fits the policy |
|---|---|---|---|
| Crash SDK (Sentry, Crashlytics…) | yes | yes | no |
| Apple's crash reports (Xcode Organizer, App Store Connect) | by Apple, per the user's analytics settings | no | yes, already in the policy |
| A report the app writes and the user sends | no | no | yes |

## Decision

1. **Settings > Report a problem** builds a plain-text report and shows **all of it** before anything can leave the device. The user may add a description, then sends it themselves: the share sheet, an email to the address in the privacy policy, or a copy. The app never sends it.
2. **Contents:** app version and build, system version, device model identifier (never the device's name), app language, connection state (connected, Wi‑Fi/wired/cellular, IPv4/IPv6, Low Data Mode, local network refused), each check's status, verdict and advice in English (not the detail lines), the last hour of per-minute counts per target, the app's own log of the last 30 minutes (target names and counts only), and short summaries of crashes and hangs the system reported through MetricKit (type, code, signal, build; no call stacks).
3. **No addresses or network names:** the app never reads the SSID or BSSID; every IPv4/IPv6 address is masked as `<address>` (candidates are confirmed with `inet_pton`), except the public resolvers 1.1.1.1, 8.8.8.8 and 9.9.9.9 that every copy of the app measures.
4. MetricKit summaries are kept on the device (the last 10, in the app's Application Support folder) only so a report can include them.
5. Crash reports from Apple (TestFlight, App Store) are used as they come, with no code of ours.

## Consequences

- ✅ Reports carry the facts a fix needs, and the user sees every character that is sent.
- ✅ No change to the "nothing is sent" promise: sending is the user's own action, like writing an email.
- ⚠️ Fewer reports than an automatic system would collect; Apple's crash reports cover crashes of users who opted in.
- ⚠️ New text in a report must keep to the rule: no addresses, names or identifiers of the user's network; `ProblemReport.redact` is the last line of defence, tested in `SteadyKitTests`.
