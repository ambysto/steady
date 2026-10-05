#!/bin/bash
# App Store screenshots of the sample network (SampleNetwork, debug builds only) on the iPhone
# 6.9" and iPad 13" Simulators, light appearance, English, a clean status bar. Writes
# apple/AppStore/screenshots/<device>/<n>-<tab>.png. The Mac's are taken by hand (README.md).
#
#   apple/AppStore/screenshots.sh
#
# Environment: STEADY_IPHONE / STEADY_IPAD to pick other simulators (name or UDID).
set -euo pipefail

apple="$(cd "$(dirname "$0")/.." && pwd)"
out="$apple/AppStore/screenshots"
derived="$apple/build/screenshots"
iphone="${STEADY_IPHONE:-iPhone 18 Pro Max}"
ipad="${STEADY_IPAD:-iPad Pro 13-inch (M5)}"
bundle="com.ambysto.steady$(sed -n 's/^STEADY_BUNDLE_ID_SUFFIX = //p' "$apple/Config/Local.xcconfig" 2>/dev/null || true)"

xcodebuild build -quiet -project "$apple/Steady.xcodeproj" -scheme Steady -configuration Debug \
    -destination "generic/platform=iOS Simulator" -derivedDataPath "$derived" CODE_SIGNING_ALLOWED=NO
app="$derived/Build/Products/Debug-iphonesimulator/Ambysto Steady.app"

shoot() {   # device folder, tabs...
    local device="$1" folder="$2"; shift 2
    xcrun simctl boot "$device" 2>/dev/null || true
    xcrun simctl bootstatus "$device" -b >/dev/null
    xcrun simctl ui "$device" appearance light
    xcrun simctl ui "$device" content_size large
    xcrun simctl status_bar "$device" override --time 9:41 --dataNetwork wifi --wifiMode active --wifiBars 3 \
        --cellularMode active --cellularBars 4 --batteryState charged --batteryLevel 100
    xcrun simctl install "$device" "$app"
    mkdir -p "$out/$folder"
    local n=1
    for tab in "$@"; do
        xcrun simctl terminate "$device" "$bundle" 2>/dev/null || true
        xcrun simctl launch "$device" "$bundle" -StoreScreenshots -StoreTab "$tab" -AppleLanguages "(en)" -AppleLocale en_US >/dev/null
        sleep 4
        xcrun simctl io "$device" screenshot "$out/$folder/$n-$tab.png" >/dev/null
        echo "$folder/$n-$tab.png"
        n=$((n + 1))
    done
    xcrun simctl status_bar "$device" clear
}

shoot "$iphone" iphone-6.9 overview diagnostics history settings
shoot "$ipad" ipad-13 overview diagnostics history
