# Code signing policy

> **Status: releases are not code-signed.** Ambysto Steady is a free, open-source project and does
> not currently use a code-signing certificate. Windows SmartScreen may therefore show an
> "unknown publisher" warning the first time you open it.

SignPath Foundation, which provides free certificates to open-source projects, declined our first
application in October 2026 because the project does not yet have enough public visibility. Signing
may be added later; this page will say so when it happens.

## Opening an unsigned release

When Windows shows "Windows protected your PC", choose **More info** → **Run anyway**. Only do this
for files you downloaded from the official
[GitHub Releases](https://github.com/ambysto/steady/releases) page.

## What you can check instead

- The release zip is built by the repository's
  [release workflow](.github/workflows/release.yml) on GitHub Actions from the tagged source, with
  the tests and a smoke test run first. Nothing is added by hand.
- The source is public under the GNU General Public License v3.0, so you can read exactly what the
  app does, or build it yourself with `python scripts/build.py`.
- The [privacy policy](PRIVACY.md) lists every network connection the app makes. It has no
  telemetry, analytics or advertising.

## Privacy policy

See the [privacy policy](PRIVACY.md): the program sends no personal data and has no telemetry,
analytics or advertising.

## Reporting

Report a suspicious or tampered binary to contact@ambysto.com or open an issue on
[GitHub](https://github.com/ambysto/steady/issues).
