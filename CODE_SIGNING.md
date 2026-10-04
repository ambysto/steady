# Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/).

> Status: application to SignPath Foundation pending. Until it is accepted, releases are not yet
> signed and Windows SmartScreen may warn when the app is opened for the first time.

## What is signed

The Windows build of **Ambysto Steady** (`Ambysto Steady.exe` and the files of the release zip)
published on [GitHub Releases](https://github.com/ambysto/steady/releases) and on
[ambysto.com](https://ambysto.com). Only binaries built from this repository by its
[release workflow](.github/workflows/release.yml) on GitHub Actions are submitted for signing; the
build runs from the tagged source with no manual step in between.

## Team roles

| Role | Who |
|---|---|
| Committers and reviewers | Members of the [ambysto](https://github.com/ambysto) GitHub account with write access to this repository |
| Approvers | Owner of the [ambysto](https://github.com/ambysto) GitHub account |

Every signing request is approved manually by an approver for each release.

## Privacy policy

See the [privacy policy](PRIVACY.md): the program sends no personal data and has no telemetry,
analytics or advertising.

## Verify a download

Right-click `Ambysto Steady.exe` → **Properties** → **Digital Signatures**: the signer is
SignPath Foundation. In PowerShell:

```powershell
Get-AuthenticodeSignature "Ambysto Steady.exe"
```

## Reporting

Report a suspicious or tampered binary to contact@ambysto.com or open an issue on
[GitHub](https://github.com/ambysto/steady/issues).
