#!/usr/bin/env bash
# Mirror each device's KOReader statistics database to the laptop, then import.
#
# The PRD suggests Syncthing for this. adb-over-wifi is the same idea with less
# setup, and it fails in a more obvious way: if a device is not reachable this
# exits having done nothing, rather than silently serving a stale copy.
#
# Devices that are simply switched off are not an error. Reading history is
# append-only per device and session ids are deterministic, so a device that
# misses ten runs loses nothing — the next successful run imports everything.
set -uo pipefail

STATS_ROOT="${SPINE_DATA_DIR:-$HOME/spine-data}/koreader-stats"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Directory name is the device id, and it is baked into every session id derived
# from that device. Renaming one forks its whole history, so this mapping is
# append-only in spirit: add rows, never edit them.
device_dir() {
    case "$1" in
        "Pixel 8")  echo "pixel" ;;
        "SM-X730")  echo "tab-s11" ;;
        *)          echo "$1" | tr '[:upper:] ' '[:lower:]-' ;;
    esac
}

mkdir -p "$STATS_ROOT"
pulled=0

for serial in $(adb devices | awk 'NR>1 && $2=="device" {print $1}'); do
    model="$(adb -s "$serial" shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
    [ -n "$model" ] || continue
    dir="$STATS_ROOT/$(device_dir "$model")"
    mkdir -p "$dir"

    # Copy to a readable path first: the KOReader settings directory is not
    # always pullable directly depending on the app's storage permissions.
    if adb -s "$serial" shell 'cp /sdcard/koreader/settings/statistics.sqlite3 /sdcard/.spine-stats.db' 2>/dev/null &&
       adb -s "$serial" pull /sdcard/.spine-stats.db "$dir/statistics.sqlite3" >/dev/null 2>&1; then
        echo "pulled $model -> $(basename "$dir")"
        pulled=$((pulled + 1))
    else
        echo "skipped $model (statistics database not readable)" >&2
    fi
    adb -s "$serial" shell 'rm -f /sdcard/.spine-stats.db' 2>/dev/null
done

if [ "$pulled" -eq 0 ]; then
    echo "no devices reachable; nothing to import"
    exit 0
fi

cd "$REPO/laptop" && exec uv run kostats import
