# ADR-0013: Apple builds are archived and uploaded from a Mac, by script

- **Status:** Accepted
- **Date:** 2026-10-05
- **Related:** [ADR-0009](0009-native-apple-and-android-apps.md), [ADR-0010](0010-apple-app-structure.md), [apple/RELEASE.md](../../apple/RELEASE.md), SIC-63

## Context

The Windows app is released by GitHub Actions on a `v*` tag (`.github/workflows/release.yml`), unsigned. Apple builds cannot be unsigned: TestFlight and the App Store need an Apple Distribution certificate, provisioning profiles of the Ambysto organization's team, and an App Store Connect upload.

Options considered:

| | Signing secrets | Effort now | Repeatable |
|---|---|---|---|
| Xcode Organizer by hand | in the developer's keychain | none | steps to remember |
| A script on a Mac (`xcodebuild archive`, `-exportArchive`) | keychain + an API key outside the repo | small | yes |
| GitHub Actions on a macOS runner | certificate, key and API key as repository secrets | larger | yes |
| Xcode Cloud | Apple-managed | setup in App Store Connect | yes |

## Decision

1. **`apple/scripts/release.sh` on a Mac** archives iOS and macOS in Release, and with `--upload` exports and uploads both to App Store Connect. Before archiving it checks:
   - the String Catalogs are generated from `app/locales`;
   - the SteadyKit tests pass;
   - the working tree is clean.
2. **Automatic signing** (`-allowProvisioningUpdates`): Xcode creates the App ID, certificates and profiles of the team given in `STEADY_TEAM_ID`. Uploads use an App Store Connect API key from the environment, or Xcode's signed-in account. **No team ID or key is committed;** `Config/ExportOptions.plist` is filled in at run time.
3. **Build number = UTC time** (`yyyymmddHHMM`), so every upload is higher than the last without a counter in the repository. `MARKETING_VERSION` stays in `Config/Base.xcconfig`.
4. A release is tagged `apple-v<version>-<build>`, separate from the Windows `v*` tags, which trigger the Windows workflow.
5. The bundle carries what review needs:
   - a privacy manifest declaring nothing (no tracking, no data collected, no APIs that need a reason);
   - `ITSAppUsesNonExemptEncryption = NO`;
   - the Mac app sandboxed with the hardened runtime.

## Consequences

- ✅ One command per TestFlight build, and no signing secrets in GitHub.
- ✅ The same script can later run on a macOS runner or Xcode Cloud if releases become frequent.
- ⚠️ A release needs a Mac with Xcode and the organization's account or API key; nothing is automatic on a tag.
- ⚠️ Until Ambysto is enrolled as an organization (D‑U‑N‑S pending), only the dry run works: personal team, `.dev` bundle ID, iOS archive, no upload.
