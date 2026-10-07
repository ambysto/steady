# App Store listing

What the App Store page of Ambysto Steady says, kept next to the code so it changes with it (SIC-76). The layout follows fastlane `deliver` (`metadata/<locale>/<field>.txt`), so it can be uploaded with that tool or pasted into App Store Connect by hand. Releasing builds is described in [../RELEASE.md](../RELEASE.md).

## Text (`metadata/en-US/`)

| Field | File | Limit |
|---|---|---|
| Name | `name.txt` | 30 |
| Subtitle | `subtitle.txt` | 30 |
| Promotional text (editable without a new build) | `promotional_text.txt` | 170 |
| Description | `description.txt` | 4000 |
| Keywords, comma-separated, not repeating the name | `keywords.txt` | 100 |
| What's New | `release_notes.txt` | 4000 |
| Support, marketing and privacy policy URLs | `support_url.txt`, `marketing_url.txt`, `privacy_url.txt` | — |

`copyright.txt` and `primary_category.txt` (Utilities) are shared by every locale. The description says only what the app does today; keep it so when features change. Every URL points at steady.ambysto.com, which has to be online before review (SIC-58).

## App Review (`metadata/review_information/`)

`notes.txt` explains the local network permission, the hosts the app contacts and the on-demand bufferbloat test. The reviewer contact (name, phone) is entered in App Store Connect and is not kept here.

## Answers in App Store Connect

- **App Privacy:** Data Not Collected (PRIVACY.md). Crash reports Apple collects for opted-in users are Apple's and need no declaration from the developer.
- **Age rating:** none of the content descriptors apply, so the rating is 4+.
- **Export compliance:** set by the build (`ITSAppUsesNonExemptEncryption = NO`).
- **Price and availability:** free, every country. Ambysto decides this.

## Screenshots (`screenshots/`, made by script, not tracked)

`apple/AppStore/screenshots.sh` builds the app in Debug and takes the iPhone and iPad screenshots on the Simulators, in English, light appearance, with a 9:41 status bar. The app then runs with `-StoreScreenshots` (and `-StoreTab <tab>`): instead of measuring, it shows `SampleNetwork`, a healthy made-up network on documentation addresses (router 192.0.2.1). For review screenshots of the Overview's check, `-SampleProblems` gives that network a fair Wi‑Fi signal (Mac), failing router DNS and about 2.5% loss past the router, and `-RunCheck` runs "Check my connection" at launch. That code exists only in Debug builds. Retake the screenshots whenever the UI changes. The Mac's are taken by hand from the same mode:
1. Open the Debug build with `--args -StoreScreenshots -StoreTab overview` (then `diagnostics`).
2. Zoom the window to fill the screen, and bring it to the front with `open -a` (Stage Manager otherwise keeps it in the strip).
3. Capture it with `screencapture -o -l <window id>`.
4. Resample to 2880 × 1800 with `sips --resampleHeightWidth 1800 2880`. A zoomed window on a 16:10 MacBook screen is within 1% of that.


| Device | Size (portrait) | Shown |
|---|---|---|
| iPhone 6.9" | 1320 × 2868 | Overview, Diagnostics, History, Report a problem |
| iPad 13" | 2064 × 2752 | Overview with the sidebar, Diagnostics, History |
| Mac | 2880 × 1800 | Overview, Diagnostics with the Mac-only checks |

Screenshots must not show real network identifiers: no public IP addresses, network names or hardware addresses (CLAUDE.md).
