#!/usr/bin/env bash
# Install the reader stack on an Android device over adb, without touching its UI.
#
#   device/android-install.sh [adb-serial]
#
# Installs Syncthing-Fork (books in, statistics out) and Readest (the reader), then
# grants the permissions each would otherwise ask for one screen at a time. Safe to
# re-run: installs are in-place upgrades and grants are idempotent.
#
# What this cannot do: Syncthing-Fork's first-run onboarding, and anything stored
# in an app's private settings. Those remain on-device steps — see
# docs/DEVICE-SETUP.md section 6.
set -euo pipefail

SYNCTHING_TAG=v2.1.5.0
SYNCTHING_REPO=researchxxl/syncthing-android
SYNCTHING_PKG=com.github.catfriend1.syncthingfork
READEST_TAG=v0.12.10
READEST_REPO=readest/readest
READEST_PKG=com.bilingify.readest

[ $# -ge 1 ] && export ANDROID_SERIAL="$1"
adb get-state >/dev/null

abi="$(adb shell getprop ro.product.cpu.abi | tr -d '\r')"
[ "$abi" = arm64-v8a ] || { echo "unsupported ABI: $abi (only arm64-v8a is wired up)" >&2; exit 1; }
echo "device: $(adb shell getprop ro.product.model | tr -d '\r')"

cache="${XDG_CACHE_HOME:-$HOME/.cache}/spine-apk"
mkdir -p "$cache"
fetch() { # repo tag pattern
    local found
    found="$(ls "$cache"/$3 2>/dev/null | head -1 || true)"
    if [ -z "$found" ]; then
        gh release download "$2" -R "$1" -p "$3" -D "$cache" >&2
        found="$(ls "$cache"/$3 | head -1)"
    fi
    echo "$found"
}

adb install -r "$(fetch "$SYNCTHING_REPO" "$SYNCTHING_TAG" "*${SYNCTHING_TAG}_arm64-v8a.apk")"
adb install -r "$(fetch "$READEST_REPO" "$READEST_TAG" "Readest_${READEST_TAG#v}_arm64.apk")"

# Both read shared storage: Syncthing writes Books/spine and reads koreader/settings,
# Readest reads Books/spine in place.
for pkg in "$SYNCTHING_PKG" "$READEST_PKG"; do
    adb shell appops set --uid "$pkg" MANAGE_EXTERNAL_STORAGE allow
    adb shell pm grant "$pkg" android.permission.POST_NOTIFICATIONS 2>/dev/null || true
done

# Syncthing has to survive in the background or books stop arriving.
adb shell dumpsys deviceidle whitelist "+$SYNCTHING_PKG" >/dev/null
adb shell cmd appops set "$SYNCTHING_PKG" RUN_ANY_IN_BACKGROUND allow

echo "installed; open Syncthing-Fork once and finish its onboarding"
