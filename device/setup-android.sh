#!/usr/bin/env bash
# Set up an Android device as a spine reader: Readest + Syncthing, wired to this
# laptop. Everything the laptop can do is done here; the handful of taps only the
# device can make are asked for one at a time, and each is checked before moving on.
#
#   device/setup-android.sh <name> [adb-serial]
#
# <name> is short and stable, e.g. `tab-s11`: it becomes the Syncthing device name
# and the reading-stats device id (`<name>-readest`). Renaming it later forks that
# device's stats history. Safe to re-run: every step checks before it acts.
set -euo pipefail

NAME="${1:?usage: device/setup-android.sh <name> [adb-serial]}"
[[ "$NAME" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "name: lowercase letters, digits, dashes" >&2; exit 1; }
[ $# -ge 2 ] && export ANDROID_SERIAL="$2"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATS_DIR="$HOME/spine-data/koreader-stats/$NAME-readest"
STATS_FOLDER="readest-stats-$NAME"
LIBRARY_FOLDER="spine-library"
# One inbox folder shared by every device: each one's Download feeds ~/inbox/phone,
# which the ingest path unit already watches.
INBOX_FOLDER="phone-inbox-pixel"
API="http://127.0.0.1:8384/rest"
KEY="$(syncthing cli config gui apikey get)"

st() { curl -sf -H "X-API-Key: $KEY" "$@"; }
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok() { printf '   \033[32m✓\033[0m %s\n' "$*"; }
ask() { printf '   \033[33m→\033[0m %s\n' "$*"; }
wait_for() { # description, command...
    local what="$1"; shift
    until "$@" >/dev/null 2>&1; do sleep 3; done
    ok "$what"
}

step "1/5  Install Readest and Syncthing-Fork, grant permissions"
"$HERE/android-install.sh" | sed 's/^/   /'

step "2/5  Syncthing first run"
if ! adb shell pidof libsyncthingnative.so >/dev/null 2>&1; then
    adb shell monkey -p com.github.catfriend1.syncthingfork -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || true
    ask "On the device: if Syncthing-Fork shows welcome screens, tap through them to the end."
fi
wait_for "Syncthing is running on the device" adb shell pidof libsyncthingnative.so

step "3/5  Pair the device with this laptop"
existing="$(st "$API/config/devices" | python3 -c "
import json,sys
print(next((d['deviceID'] for d in json.load(sys.stdin) if d['name']=='$NAME'), ''))")"
if [ -n "$existing" ]; then
    DEVICE="$existing"
    ok "already paired as $NAME"
else
    LAPTOP_ID="$(st "$API/system/status" | python3 -c 'import json,sys; print(json.load(sys.stdin)["myID"])')"
    ask "On the device: Syncthing-Fork → Devices → + → scan this QR (or paste the ID) → Create."
    if command -v qrencode >/dev/null; then qrencode -t ANSIUTF8 "$LAPTOP_ID" | sed 's/^/     /'; fi
    echo "     $LAPTOP_ID"
    known="$(st "$API/config/devices" | python3 -c 'import json,sys; print(" ".join(d["deviceID"] for d in json.load(sys.stdin)))')"
    DEVICE=""
    while [ -z "$DEVICE" ]; do
        sleep 3
        DEVICE="$(st "$API/cluster/pending/devices" | python3 -c "
import json,sys
known = set('$known'.split())
print(next((d for d in json.load(sys.stdin) if d not in known), ''))")"
    done
    syncthing cli config devices add --device-id "$DEVICE" --name "$NAME"
    ok "paired: $NAME ($DEVICE)"
fi

step "4/5  Laptop side: stats folder, and share library + inbox"
mkdir -p "$STATS_DIR"
if ! syncthing cli config folders list | grep -qx "$STATS_FOLDER"; then
    syncthing cli config folders add --id "$STATS_FOLDER" --label "Readest stats ($NAME)" \
        --path "$STATS_DIR" --type receiveonly
fi
cat > "$STATS_DIR/.stignore" <<'EOF'
// Only the statistics database. The device shares Readest's whole data
// directory (Books/Readest); nothing else in it belongs on the laptop.
!/statistics.db
!/statistics.db-wal
!/statistics.db-shm
*
EOF
for folder in "$LIBRARY_FOLDER" "$INBOX_FOLDER" "$STATS_FOLDER"; do
    if ! syncthing cli config folders "$folder" devices list | grep -qx "$DEVICE"; then
        syncthing cli config folders "$folder" devices add --device-id "$DEVICE"
    fi
done
ok "shared $LIBRARY_FOLDER, $INBOX_FOLDER, $STATS_FOLDER with $NAME"

connected() {
    st "$API/system/connections" |
        python3 -c "import json,sys; sys.exit(not json.load(sys.stdin)['connections'].get('$DEVICE', {}).get('connected'))"
}
accepted() { # folder
    st "$API/db/completion?folder=$1&device=$DEVICE" |
        python3 -c 'import json,sys; sys.exit(json.load(sys.stdin).get("remoteState") != "valid")'
}
step "5/5  On the device: accept the three shares (notifications, or ☰ → Web GUI)"
wait_for "device connected" connected
sleep 5  # let the device report which folders it already has
accepted "$LIBRARY_FOLDER" || ask "spine library       → /storage/emulated/0/Books/spine     · Receive Only"
accepted "$STATS_FOLDER"   || ask "Readest stats       → /storage/emulated/0/Books/Readest   · Send Only"
accepted "$INBOX_FOLDER"   || ask "Phone downloads     → type /storage/emulated/0/Download in the Web GUI · Send Only"
wait_for "library accepted" accepted "$LIBRARY_FOLDER"
wait_for "stats accepted" accepted "$STATS_FOLDER"
wait_for "downloads accepted" accepted "$INBOX_FOLDER"
adb shell "mkdir -p /storage/emulated/0/Books/spine && printf '*.sdr\n' > /storage/emulated/0/Books/spine/.stignore"

cat <<EOF

$(printf '\033[1m== Readest, on the device (its settings are private to the app)\033[0m')
   1. Settings → Integrations → KOReader Sync
        Server https://kirtanjain.com · your username and password · Device Name: $NAME
        Connect, then turn on Send Document Metadata
   2. ☰ → Advanced Settings → Change Data Location → /sdcard/0/Books
   3. + → Import from Folder → Books/spine · Read books in place ✓ · Import all into library
   4. Syncthing-Fork → Folders → spine library → File Versioning → No File Versioning
EOF
wait_for "Readest's data is in Books/Readest (step 2 done)" \
    adb shell test -f /storage/emulated/0/Books/Readest/statistics.db
echo
ok "done. Reading positions sync as soon as you read; stats reach the laptop nightly."
