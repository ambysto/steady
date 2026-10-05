#!/bin/bash
# Archives Ambysto Steady for iOS/iPadOS and macOS, and with --upload sends both to App Store
# Connect for TestFlight (apple/RELEASE.md, ADR-0013). Runs on a Mac with Xcode; nothing here
# holds a secret: the team ID and the App Store Connect API key come from the environment.
#
#   STEADY_TEAM_ID=ABCDE12345 apple/scripts/release.sh            # archive only
#   STEADY_TEAM_ID=ABCDE12345 apple/scripts/release.sh --upload   # archive and upload
#
# Environment:
#   STEADY_TEAM_ID           the Ambysto organization's team ID (required)
#   STEADY_BUILD             build number (default: UTC time as year.monthday.hourminute, such as
#                            2026.1005.1830: always increasing, three integers of at most 4 digits)
#   STEADY_PLATFORMS         "iOS macOS" (default) or one of them
#   STEADY_BUNDLE_ID_SUFFIX  empty for a release; ".dev" for a dry run with a personal team
#   STEADY_ALLOW_DIRTY=1     archive uncommitted changes (never for an upload)
#   ASC_KEY_PATH, ASC_KEY_ID, ASC_ISSUER_ID
#                            App Store Connect API key; without it Xcode's signed-in account is used
set -euo pipefail

upload=false
for arg in "$@"; do
    case "$arg" in
        --upload) upload=true ;;
        *) echo "usage: $0 [--upload]" >&2; exit 2 ;;
    esac
done

apple="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(dirname "$apple")"
team="${STEADY_TEAM_ID:?set STEADY_TEAM_ID to the team that signs the release}"
stamp="$(TZ=UTC date +%Y%m%d%H%M)"
build="${STEADY_BUILD:-${stamp:0:4}.$((10#${stamp:4:4})).$((10#${stamp:8:4}))}"
platforms="${STEADY_PLATFORMS:-iOS macOS}"
suffix="${STEADY_BUNDLE_ID_SUFFIX-}"
out="$apple/build/release/$build"

if ! [[ "$build" =~ ^[0-9]{1,4}(\.[0-9]{1,4}){0,2}$ ]]; then
    echo "Build number $build: use up to three integers of at most 4 digits, such as 2026.1005.1830." >&2
    exit 2
fi

if [[ -n "$(git -C "$repo" status --porcelain)" ]]; then
    if [[ "${STEADY_ALLOW_DIRTY:-}" != 1 || "$upload" == true ]]; then
        echo "The working tree has uncommitted changes: a release is built from a commit." >&2
        exit 1
    fi
fi
if [[ "$upload" == true && -n "$suffix" ]]; then
    echo "An upload uses the production bundle ID: unset STEADY_BUNDLE_ID_SUFFIX." >&2
    exit 1
fi

auth=()
if [[ -n "${ASC_KEY_PATH:-}" ]]; then
    auth=(-authenticationKeyPath "$ASC_KEY_PATH" -authenticationKeyID "${ASC_KEY_ID:?}"
          -authenticationKeyIssuerID "${ASC_ISSUER_ID:?}")
fi

echo "== Checks"
"${PYTHON:-python3}" "$repo/scripts/locales_to_xcstrings.py" --check
(cd "$apple/SteadyKit" && swift test --quiet)

mkdir -p "$out"
version="$(sed -n 's/^MARKETING_VERSION = //p' "$apple/Config/Base.xcconfig")"
echo "== Ambysto Steady $version ($build) for $platforms"

options="$out/ExportOptions.plist"
cp "$apple/Config/ExportOptions.plist" "$options"
plutil -replace teamID -string "$team" "$options"

for platform in $platforms; do
    archive="$out/Steady-$platform.xcarchive"
    xcodebuild archive -quiet \
        -project "$apple/Steady.xcodeproj" -scheme Steady -configuration Release \
        -destination "generic/platform=$platform" -archivePath "$archive" \
        -allowProvisioningUpdates ${auth[@]+"${auth[@]}"} \
        DEVELOPMENT_TEAM="$team" STEADY_BUNDLE_ID_SUFFIX="$suffix" CURRENT_PROJECT_VERSION="$build"
    echo "archived $archive"
    if [[ "$upload" == true ]]; then
        xcodebuild -exportArchive -archivePath "$archive" -exportOptionsPlist "$options" \
            -exportPath "$out/export-$platform" -allowProvisioningUpdates ${auth[@]+"${auth[@]}"}
        echo "uploaded $platform build $build"
    fi
done

if [[ "$upload" == true ]]; then
    echo "Next: tag the commit (git tag-utc apple-v$version-$build) and add testers in App Store Connect."
fi
