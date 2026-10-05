# ADR-0010: Apple app structure, generated String Catalog and shared diagnosis test vectors

- **Status:** Accepted
- **Date:** 2026-10-05
- **Related:** [ADR-0009](0009-native-apple-and-android-apps.md) (native apps, share data not code), [ADR-0006](0006-i18n.md) (catalogs, messages)

## Context

ADR-0009 settled on one SwiftUI multiplatform app for iPhone, iPad and macOS that shares the UI catalogs and the diagnosis rules' test vectors with the Windows version as data. Before the first line of Swift we need to fix where the code lives, which platforms and versions it targets, how `app/locales/*.json` becomes an Apple String Catalog, and what a test vector looks like. Constraints:

- The Windows app (`app/`, `web/`) keeps working untouched; Apple code must not leak into it.
- Signing starts with a free personal Apple ID while the Ambysto organization enrolls in the Apple Developer Program (D-U-N-S pending). Tracked files never contain a personal team ID.
- macOS exposes Wi‑Fi details through CoreWLAN (RSSI, channel, band, link rate, scan), which only a native macOS app can use, not Mac Catalyst or "Designed for iPad".

## Decision

1. **Layout**

   ```
   apple/
   ├── Steady.xcodeproj    one multiplatform app target "Steady"
   ├── Steady/             SwiftUI views, a thin layer (folder-synchronized group)
   │   └── Resources/      Localizable.xcstrings, InfoPlist.xcstrings (generated), assets
   ├── SteadyKit/          local Swift package: rules, models, network readers, localization runtime
   └── Config/             Base.xcconfig (tracked), Local.xcconfig (untracked, per developer)
   spec/diagnosis/         rule test vectors shared by every platform
   scripts/locales_to_xcstrings.py
   ```

   Logic lives in `SteadyKit` so it runs under `swift test` without a simulator or signing. The Xcode project uses folder-synchronized groups (Xcode 16+), which keeps `project.pbxproj` small and merge-friendly, so no project generator (XcodeGen, Tuist) is needed.

2. **Platforms:** one target with native destinations iPhone, iPad and Mac (`SUPPORTED_PLATFORMS = iphoneos iphonesimulator macosx`; no Mac Catalyst, no "Designed for iPad"). Minimum **iOS/iPadOS 26.0 and macOS 26.0**: there are no existing users to keep, and macOS 26 is the newest version the development Mac runs. Swift 6 language mode with complete concurrency checking.

3. **Identifiers and signing:** bundle ID `com.ambysto.steady` on all platforms. `Base.xcconfig` holds every shared setting and ends with `#include? "Local.xcconfig"`, which sets `DEVELOPMENT_TEAM` and, while signing with a personal team, `STEADY_BUNDLE_ID_SUFFIX = .dev`. Bundle IDs are unique across all teams, so the real ID is never registered under a personal team; it stays free for the Ambysto team. `Local.xcconfig.example` documents the keys.

4. **Localization:** `app/locales/*.json` stays the single source (ADR-0006). `scripts/locales_to_xcstrings.py` (standard library only) writes:
   - `Localizable.xcstrings` — keys under the prefixes listed in the script, every language, `extractionState: manual`. Named placeholders become positional `%N$@` in the order they first appear in English, so translations may reorder words; a literal `%` becomes `%%`. A plural entry (`{"one", "other"}`) becomes a substitution on `count` (`%lld`), so Apple applies each language's own plural rules.
   - `InfoPlist.xcstrings` — Info.plist texts: `CFBundleDisplayName` from `app.name`, permission prompts such as `NSLocalNetworkUsageDescription`.
   - `Config/Generated.xcconfig` — the English values of those Info.plist texts, included by `Base.xcconfig` (a key must exist in Info.plist for its translations to apply).
   - `SteadyKit/…/MessageCatalog.swift` — for each key, the ordered parameter names with their English format spec (`.1f`, `.0%`…). Swift formats numbers itself (locale decimal separator, as in ADR-0006) and passes every value except a plural `count` as a string.

   A key may have an Apple wording under `apple.<key>` (no "PC", no background monitor), with exactly the same parameters; the Swift `Localizer` shows it instead of `<key>`, while stored messages keep the shared key. The script refuses a variant whose parameters differ.

   The generated files are committed and never edited by hand; `tests/test_apple_catalog.py` fails when they are out of date. Diagnosis results are `Message(key, params)` values in Swift too, rendered when displayed.

5. **Diagnosis test vectors:** one JSON file per rule in `spec/diagnosis/`, for example `ping.json`:

   ```json
   {"rule": "ping", "check": 5, "reference": "app/diagnostics.py evaluate_ping",
    "cases": [{"name": "...", "input": {...}, "expected": {"status": "ok", "summary": {"key": "...", "params": {}}, "details": [...], "advice": ""}}]}
   ```

   `expected` is the rule's full output with messages as `{key, params}` (an empty string where Python returns no message). Numbers compare with a 1e-9 tolerance. `tests/test_diagnosis_vectors.py` runs the cases against the Python `evaluate_*` function and `SteadyKitTests` against the Swift port, so any change to a rule must change the vectors and every implementation together.

## Consequences

- ✅ Rules and localization logic are tested on the command line (`swift test`, `python -m unittest`) on any Mac.
- ✅ Texts and verdicts stay identical across Windows and Apple; a forgotten regeneration or a rule drift fails a test.
- ✅ Personal-team builds cannot claim the production bundle ID.
- ⚠️ New Apple-only texts are still added to `app/locales/*.json` (all 9 languages) and then regenerated, never added in Xcode's catalog editor.
- ⚠️ Only rules whose inputs exist on Apple platforms are ported; vectors are written per rule as each one is ported.
- ⚠️ Raising the minimum OS later is easy; lowering it may require replacing newer APIs.
