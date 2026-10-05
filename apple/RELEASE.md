# Releasing the Apple app (TestFlight)

How Ambysto Steady for iPhone, iPad and Mac gets to testers, per [ADR-0013](../docs/adr/0013-apple-release-from-a-mac.md). Builds are archived and uploaded from a Mac with [`scripts/release.sh`](scripts/release.sh); nothing in the repository holds a team ID or a key.

## What the build already does

- One App Store Connect app for both platforms: bundle ID `com.ambysto.steady`, "Ambysto Steady.app".
- **Mac:** sandboxed (required for TestFlight and the Mac App Store), hardened runtime, outgoing connections only.
- **Export compliance:** `ITSAppUsesNonExemptEncryption = NO`, since the app uses only the system's HTTPS. App Store Connect does not ask about encryption on each upload.
- **Privacy manifest:** `Steady/PrivacyInfo.xcprivacy` declares no tracking and no collected data. The app uses none of the APIs that need a stated reason; check `nm -u` on the binary when adding code that touches UserDefaults, file timestamps, boot time, disk space or keyboards.
- **Build numbers:** a UTC timestamp (`yyyymmddHHMM`), so every upload is higher than the last. `MARKETING_VERSION` in `Config/Base.xcconfig` is the version users see.
- dSYMs are uploaded with each build, so crash reports are symbolicated.

## One-time setup for the Ambysto organization

Someone with the authority to sign for the company does these steps; the app cannot.

1. **D‑U‑N‑S number** for Ambysto. Apple's [D‑U‑N‑S lookup](https://developer.apple.com/enroll/duns-lookup/) finds or requests one at no cost. Use the legal name and address exactly as registered; a new number can take several business days.
2. **Apple Developer Program, as an Organization:** the legal entity name, the D‑U‑N‑S number, a website on the company's own domain, and an Apple Account with two-factor authentication. Enrolling as an organization shows "Ambysto" as the seller on the App Store. The membership has a yearly fee.
3. **App Store Connect > Apps > New App:**
   - platforms iOS and macOS;
   - name "Ambysto Steady";
   - primary language English (U.S.);
   - bundle ID `com.ambysto.steady`, which automatic signing registers on the first archive;
   - any SKU, for example `steady`.
4. **App Privacy:** "Data Not Collected". Nothing leaves the device unless the user sends a problem report themselves (ADR-0012), which the app does not do on its own.
   - Privacy policy URL: `https://steady.ambysto.com/privacy`. It must be online before external testing; the site is not deployed yet (SIC-58).
5. **API key for uploads:** App Store Connect > Users and Access > Integrations > Team Keys, with the Developer role. Keep the `.p8` file outside the repository; it can be downloaded only once.
6. **Development signing:** put the organization's team ID in `Config/Local.xcconfig` and remove `STEADY_BUNDLE_ID_SUFFIX` from it, so local builds use the real bundle ID. The personal team's `.dev` builds can then be deleted from devices.

Never register `com.ambysto.steady` under a personal team: bundle IDs are unique across all teams, and the organization could not use it afterwards.

## Each TestFlight build

1. Start from a clean commit on `main` that has a CHANGELOG entry. Raise `MARKETING_VERSION` when the version changes.
2. Run:

   ```bash
   STEADY_TEAM_ID=… ASC_KEY_PATH=… ASC_KEY_ID=… ASC_ISSUER_ID=… apple/scripts/release.sh --upload
   ```

   The script then:
   - checks that the String Catalogs match `app/locales`;
   - runs the SteadyKit tests;
   - archives iOS and macOS in Release;
   - exports and uploads both builds.

   Without the `ASC_*` variables it uses the account signed in to Xcode.
3. App Store Connect processes the builds, usually within minutes.
   - **Internal testers** (members of the team, up to 100) can install them at once.
   - **External testers** need Beta App Review for the first build of each version. Write "What to Test", and explain the local network permission: the app only pings the router.
4. Tag the commit: `git tag-utc apple-v<version>-<build>`, then push the tag as `ambysto`.

## Crashes and feedback

- **Xcode > Window > Organizer > Crashes:** crashes and hangs from TestFlight and App Store users who share analytics with developers.
- **App Store Connect > TestFlight > Feedback:** testers' screenshots and comments.
- **Settings > Report a problem** in the app: a text report the user sends themselves (ADR-0012).

## Dry run without the organization

Archives iOS only, with the personal team and the `.dev` bundle ID; nothing is uploaded:

```bash
STEADY_TEAM_ID=<personal team> STEADY_BUNDLE_ID_SUFFIX=.dev STEADY_PLATFORMS=iOS STEADY_ALLOW_DIRTY=1 apple/scripts/release.sh
```

A macOS archive with a personal team may register the Mac in that team; an ad-hoc archive (`CODE_SIGN_IDENTITY=- DEVELOPMENT_TEAM=`) checks the Release build without it.
