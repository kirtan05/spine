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
