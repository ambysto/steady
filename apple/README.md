# Ambysto Steady for iPhone, iPad and Mac

One SwiftUI multiplatform app (ADR-0009). Layout, versions, signing and how texts and rules are shared with the Windows version: [ADR-0010](../docs/adr/0010-apple-app-structure.md). What iOS allows compared with Android and Windows: [docs/MOBILE.md](../docs/MOBILE.md).

| Path | Content |
|---|---|
| `Steady.xcodeproj` | One app target `Steady`: iPhone, iPad, native Mac. Minimum iOS/iPadOS/macOS 26.0 |
| `Steady/` | SwiftUI views. `Resources/*.xcstrings` are generated, do not edit them in Xcode |
| `SteadyKit/` | Swift package with everything testable: localization runtime, network path model, diagnosis rules |
| `Config/Base.xcconfig` | Shared build settings; `Local.xcconfig` (not tracked) adds your team ID |

Requirements: Xcode 27 or later on a Mac.

## First-time setup

1. Copy `Config/Local.xcconfig.example` to `Config/Local.xcconfig` and set `DEVELOPMENT_TEAM`. With a free personal team keep `STEADY_BUNDLE_ID_SUFFIX = .dev`, so the app is `com.ambysto.steady.dev` and the production ID stays free for the Ambysto team.
2. Xcode > Settings > Accounts: sign in with the Apple ID of that team.
3. Open `Steady.xcodeproj`, choose the `Steady` scheme and a destination (My Mac, a connected iPad...).

On an iPad or iPhone, the first run also needs Developer Mode (Settings > Privacy & Security) and, with a personal team, trusting the developer profile (Settings > General > VPN & Device Management). Apps signed by a personal team expire after 7 days; run them again from Xcode.

## Everyday commands

```bash
swift test --package-path apple/SteadyKit                 # rules and localization, no simulator needed
python scripts/locales_to_xcstrings.py                     # after any change in app/locales/*.json
xcodebuild -project apple/Steady.xcodeproj -scheme Steady -destination 'generic/platform=macOS' CODE_SIGNING_ALLOWED=NO build
xcodebuild -project apple/Steady.xcodeproj -scheme Steady -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```

## Texts

Every user-visible string comes from `app/locales/*.json`. To add one: add the key to `en.json` and the other 8 catalogs, run `python scripts/locales_to_xcstrings.py`, then render it with `Localizer` (`text("ui.path.connected")`, or a `Message` with parameters). Pass strings to SwiftUI as values (`Text(text(...))`), never as literals, so Xcode does not extract or look up keys on its own.

## Diagnosis rules

A rule is ported from `app/diagnostics.py` together with its vectors in `spec/diagnosis/`. The Swift tests (`DiagnosisVectorTests`) and `tests/test_diagnosis_vectors.py` run the same cases; a change to a rule changes the vectors and both implementations.
