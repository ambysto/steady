#!/usr/bin/env bash
# Builds the current macOS app and a separate native installer app into a local DMG.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APPLE="$ROOT/apple"
BUILD="$APPLE/build/installer-dmg"
DIST="$ROOT/dist"
VERSION="$(sed -n 's/^MARKETING_VERSION = //p' "$APPLE/Config/Base.xcconfig")"
APP="$DIST/Ambysto Steady.app"
INSTALLER_APP="$DIST/Install Ambysto Steady.app"
DMG="$DIST/AmbystoSteady-$VERSION-installer.dmg"

mkdir -p "$DIST"
INSTALLER_FLAGS=(--package-path "$APPLE/Installer" -c release --arch arm64 --arch x86_64)
swift build "${INSTALLER_FLAGS[@]}"
INSTALLER_BIN="$(swift build "${INSTALLER_FLAGS[@]}" --show-bin-path)/AmbystoInstaller"

xcodebuild -quiet -project "$APPLE/Steady.xcodeproj" -scheme Steady -configuration Release \
  -destination 'generic/platform=macOS' -derivedDataPath "$BUILD" \
  CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM= build
BUILT_APP="$BUILD/Build/Products/Release/Ambysto Steady.app"
if [[ ! -d "$BUILT_APP" ]]; then
  echo "error: Xcode did not produce $BUILT_APP" >&2
  exit 1
fi

rm -rf "$APP" "$INSTALLER_APP"
cp -R "$BUILT_APP" "$APP"
mkdir -p "$INSTALLER_APP/Contents/MacOS" "$INSTALLER_APP/Contents/Resources"
cp "$INSTALLER_BIN" "$INSTALLER_APP/Contents/MacOS/AmbystoInstaller"
cp "$APPLE/Installer/Resources/InstallerInfo.plist" "$INSTALLER_APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$INSTALLER_APP/Contents/Info.plist"

codesign --verify --deep --strict "$APP"
codesign --force --sign - "$INSTALLER_APP"
codesign --verify --deep --strict "$INSTALLER_APP"

STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
cp -R "$INSTALLER_APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Ambysto Steady" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
echo "Created $DMG"
