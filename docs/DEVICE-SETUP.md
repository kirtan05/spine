# KOReader device setup

Three devices: Pixel, Tab S11, tablet 2. Do all of this on **every** device, and
do it **before downloading any books**.

Two of these settings are easy to skip and expensive to fix later.

---

## 1. Point at the sync server

**⚙ → Tools → Progress sync → Custom sync server**

```
https://kirtanjain.com
```

No trailing path. The plugin appends `/users/...` and `/syncs/...` itself.

## 2. Register or log in

On the **first** device: **Register**. Registration is open only while the `users`
table is empty and closes itself immediately afterwards.

On the **other two**: **Login** with the same credentials.

Use a random credential used nowhere else. The protocol sends an unsalted MD5 of
the password in a header on every request — that is inherited from kosync and no
server implementation can fix it. A single-purpose random credential makes it not
matter.

## 3. Document matching method — confirm Binary

**Progress sync → Document matching method**

There are exactly two options, and the full labels are:

- **"Binary. Only identical files will be kept in sync."** ← this one
- "Filename. Files with matching names will be kept in sync."

**Binary is already the default** (`checksum_method = CHECKSUM_METHOD.BINARY`, in
the plugin's defaults), so this step is a check rather than a change. Confirm the
tick is on Binary and move on.

Binary identifies a book by a partial MD5 of its contents. Filename matching would
fork progress the moment a file was renamed, and would merge two different books
that happened to share a name.

Whatever you pick, it must be **the same on every device** or they will not
recognise each other's books at all.

## 4. Automatically keep documents in sync → **on**

**Progress sync → Automatically keep documents in sync**

`auto_sync` defaults to **false**, and with it off the plugin attaches no event
handlers at all — `registerEvents()` sets `onCloseDocument` and `onSuspend` to
`nil`, and `onReaderReady` skips the pull. Closing a book does nothing, opening
one does nothing, and the server stays empty with no error anywhere. This is the
setting that actually makes sync happen.

Watch for one thing on e-ink devices: at startup the plugin force-disables
auto-sync if `wifi_enable_action` is anything other than `turn_on`, logging
"Automatic sync has been disabled because wifi_enable_action is *not* turn_on".
That guard does not fire on Android, but if auto-sync keeps turning itself off,
set **Network → action when Wi-Fi is off → turn on automatically**.

## 5. Send document metadata — not available yet

**There is no such setting in KOReader v2026.03.** The metadata push exists on
master but has not shipped: the word "metadata" appears nowhere in the release's
`kosync.koplugin`, and its `api.json` lists only
`document, progress, percentage, device, device_id`.

**So on current KOReader, the ingest pipeline is the _only_ source of titles**,
not the enrichment path the PRD assumed. Any book that has not gone through
`shelf` renders as "Unknown" on the reading page — which remains the correct
signal, it is just now the normal state for anything sideloaded by hand.

The server already accepts and stores the field, so nothing needs changing when a
release does ship it. Setting `send_metadata = true` ahead of time is harmless and
means it turns on by itself at upgrade.

## 6. Get the library onto the device — Syncthing

Kavita runs on the laptop, so it can only hand out a book while the laptop is
awake and reachable — exactly when you are *not* reading. Syncthing instead pushes
every published book onto the device ahead of time, so opening one needs no
network at all. The same link carries KOReader's statistics database back.

| Folder id | Laptop | Device | Carries |
|---|---|---|---|
| `spine-library` | `~/library`, **send only** | `Books/spine`, **receive only** | books → device |
| `koreader-stats-<device>` | `~/spine-data/koreader-stats/<device>`, **receive only** | `koreader/settings`, **send only** | `statistics.sqlite3*` → laptop |

Neither side can write into the other's source of truth. The laptop's stats folder
has a `.stignore` that admits only `statistics.sqlite3` and its `-wal`/`-shm`
sidecars, so the rest of KOReader's settings never leaves the device.

**Do not turn on file versioning** for the stats folders. It keeps old copies under
`.stversions/`; `kostats` skips hidden directories, but there is no reason to have
them.

### On the device

1. Install **Syncthing-Fork** from F-Droid. It is not on the Play Store — Google
   no longer allows the "all files access" permission Syncthing needs, and the
   original Syncthing Android app is discontinued.
2. Grant **All files access**, and set the app's battery usage to **Unrestricted**
   or Android will kill it in the background.
3. **Devices → +** and add the laptop's device ID
   (`syncthing cli show system | grep myID` on the laptop).
4. Send the device's own ID to the laptop (**⋮ → Show device ID**), then run the
   laptop step below.
5. Accept both folder shares when they appear:
   - `spine library` → `/storage/emulated/0/Books/spine`, folder type
     **Receive Only**
   - `KOReader stats (<device>)` → `/storage/emulated/0/koreader/settings`,
     folder type **Send Only**
6. In KOReader: **File browser → long-press `Books/spine` → Set as HOME folder**.
7. Give the device's library folder its own ignore file, so KOReader's `.sdr`
   sidecars (highlights, notes, position) are never treated as local changes to a
   receive-only folder — "Revert local changes" would otherwise delete them:

   ```fish
   printf '*.sdr\n' > /tmp/stignore
   adb push /tmp/stignore /storage/emulated/0/Books/spine/.stignore
   ```

   No `(?d)` prefix, deliberately: a refile that removes a book's folder then
   leaves the orphaned notes behind instead of deleting them.

### On the laptop

```fish
set id <DEVICE-ID>; set name pixel     # the koreader-stats/ directory name
syncthing cli config devices add --device-id $id --name $name
syncthing cli config folders spine-library devices add --device-id $id
syncthing cli config folders koreader-stats-$name devices add --device-id $id
```

`<name>` must match the directory `collect-stats.sh` already uses for that device
(`pixel`, `tab-s11`). A new name starts a new device history.

The stats folder for a new device has to exist first — copy the
`koreader-stats-pixel` setup: `syncthing cli config folders add --id
koreader-stats-$name --path ~/spine-data/koreader-stats/$name --type receiveonly`,
plus the same `.stignore`.

Books arrive as soon as both ends are online; the nightly `kostats-import` timer
imports whatever statistics have arrived, plugged in or not.

## 7. Readest — the primary reader

Readest replaces KOReader for day-to-day reading. It speaks the same kosync
protocol and identifies a book by the same partialMD5 (checked: its hash of
`laptop/tests/fixtures/koreader-verified.epub` is KOReader's on-device value), so
the server needs nothing. `device/android-install.sh` installs it.

1. **Settings → Integrations → KOReader Sync**: server `https://kirtanjain.com`,
   same username and password, **Checksum Method: File Content**, **Send Document
   Metadata** on. Connect registers the account if it does not exist yet.
2. **Library menu → Change Data Location → `/sdcard/0/Books`**. This moves
   Readest's data — including `statistics.db` — to
   `/storage/emulated/0/Books/Readest`, beside `Books/spine` — one folder for
   everything reading-related, where Syncthing can read it.
3. **Import → From Folder → `Books/spine`**, read in place, auto-import on. Books
   are not copied; subfolders become groups.
4. Accept the Syncthing share **Readest stats (<device>)** as
   `/storage/emulated/0/Books/Readest`, type **Send Only**. The laptop side is
   a receive-only folder at `~/spine-data/koreader-stats/<device>-readest` whose
   `.stignore` admits only `statistics.db*`.

Readest writes KOReader's statistics schema to `statistics.db`; `kostats` reads
either file and records the reader as the session's `source` (`koreader` or
`readest`). Keep each reader's database in its own directory — the directory name
is the device id, and two readers under one id would merge their histories.

## 8. Phone downloads → the library

A book downloaded on the phone should end up in the library without being moved
by hand. The phone's `Download` folder is shared **send-only** to the laptop's
`~/inbox/phone` (**receive-only**), which the ingest path unit also watches.

The laptop side's `.stignore` admits only `*.epub`, `*.cbz` and `*.cbr` at the top
level. **PDFs are excluded deliberately**: most PDFs in Downloads are not books,
and one that reached the inbox would be published as one. Everything else in
Downloads is never transferred.

Send-only means nothing on the laptop can delete from the phone: the pipeline
removes the inbox copy after publishing, and the phone keeps its download.

On the device, accept the share **Phone downloads → spine inbox** as
`/storage/emulated/0/Download`, type **Send Only**. On the laptop the folder is
`phone-inbox-<device>`; see the `syncthing cli` commands in section 6 for adding
one for another device.

---

## Verify it actually works

1. Open a book on the Pixel, read a few pages, **close the book**
2. On the Tab S11, open the same file
3. It should offer to sync to the Pixel's position

Then check the server saw it:

```fish
npx wrangler d1 execute spine --remote --command \
  "SELECT doc_hash, device, percentage, datetime(updated_at,'unixepoch') FROM progress ORDER BY updated_at DESC LIMIT 5;"
```

And that metadata arrived:

```fish
npx wrangler d1 execute spine --remote --command \
  "SELECT doc_hash, title, authors FROM documents LIMIT 5;"
```

If `documents` is empty but `progress` is not, step 4 was missed on that device.

---

## Protocol behaviour to expect, not to fix

**Pushes happen on document close and on device suspend, debounced to 25 seconds.**
A book left open on an awake device will not propagate until one of those happens.
This is client-side and correct; do not go looking for a server bug when a position
seems stale.

**Pulls are debounced the same way.** Pulling twice within 25 seconds does nothing
the second time.

**Failed pushes are queued and retried** — except on 401, which is discarded. That
is why returning the wrong status code for a non-auth problem is so costly: a 403
where a 402 belongs reads as an auth failure and the progress is dropped.

---

## The rule that matters most

**Do not touch published files.**

Binary matching keys on the contents of the file. Re-tagging, recompressing, or
converting a file that is already on a device turns it into a different book with
separate progress — silently, and irreversibly for the history already recorded
against the old hash.

Normalisation happens in the ingest pipeline, before publish. Never after.

Kavita must also be configured to never write metadata back into files. A library
server that "helpfully" updates ComicInfo.xml changes hashes, which is the same
failure with a different cause.
