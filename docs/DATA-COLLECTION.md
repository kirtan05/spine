# Data collection runbook

Everything the importers need, and the order to collect it in.

All of it lands under `~/spine-data/`. Create that first:

```fish
mkdir -p ~/spine-data/{takeout,wellbeing,chrome,koreader-stats,playbooks}
```

---

## Priority order

Two of these are decaying right now. The rest will wait indefinitely.

| # | Source | Urgency | Why |
|---|---|---|---|
| 1 | Digital Wellbeing (`adb`) | **Today** | Daily buckets survive about a week. Every day you wait, resolution is permanently gone. |
| 2 | Google Takeout | **Start today** | Not decaying, but Google takes hours to days to build the archive. Request it early, collect it later. |
| 3 | Chrome download history | Whenever | A local SQLite file. Stable. |
| 4 | Play Books library order | Before you stop using the app | Ordering is live state; it changes as you open books. |
| 5 | KOReader `statistics.sqlite3` | Ongoing | This is the standing source of *measured* time, set up once and then automatic. |

Sources 1–4 are one-offs for reconstructing the past. Source 5 is the one that
matters long-term — six months of measured data will be worth more than all of
the reconstruction combined.

---

## 1. Digital Wellbeing / usagestats (Phase 3D)

Gives per-day foreground minutes for the Play Books app. Not per-book: there is no
per-book attribution anywhere in this source, which is why anything derived from
it is written as `confidence = 'inferred'`.

**Retention is per bucket, not one number.** Roughly: daily ~1 week, weekly ~1
month, monthly ~6 months, yearly ~2 years. Capture every bucket in one export.

### Setup

On the Pixel: **Settings → About phone → tap Build number 7×**, then
**Settings → System → Developer options → USB debugging**. Plug in over USB and
accept the RSA fingerprint prompt on the phone.

```fish
adb devices                 # must show a device as "device", not "unauthorized"
```

### Export

```fish
cd ~/spine-data/wellbeing

# Everything, all buckets. This is the one that matters — do not filter it.
adb shell dumpsys usagestats > usagestats-full.txt

# Some Android versions scope to a user; harmless if it duplicates the above.
adb shell dumpsys usagestats --user 0 > usagestats-user0.txt

# Machine-readable variant, if this build supports it (may be empty — fine).
adb shell dumpsys usagestats --checkin > usagestats-checkin.txt
```

### Verify before unplugging

```fish
wc -l ~/spine-data/wellbeing/usagestats-full.txt
grep -c "com.google.android.apps.books" ~/spine-data/wellbeing/usagestats-full.txt
grep -oE "In-memory (daily|weekly|monthly|yearly) stats" ~/spine-data/wellbeing/usagestats-full.txt | sort -u
```

Expect all four bucket headings and a non-zero count for the Play Books package
(`com.google.android.apps.books`). If the daily section is empty or the package
never appears, say so — that answers PRD open question 2 in the negative and Phase
3D is not worth even a one-off script.

**Do this on the Pixel only.** The tablets never ran Play Books.

---

## 2. Google Takeout (Phase 3A files, Phase 3B annotations)

### Request

1. <https://takeout.google.com>
2. **Deselect all**, then select **Google Play Books** only
3. Export once, `.zip`, largest file size available
4. Wait for the email. This can take hours to days for a large library.

### Unpack

```fish
cd ~/spine-data/takeout
unzip ~/Downloads/takeout-*.zip
find . -type d -maxdepth 3 | head -40
```

You should see both the uploaded book files and a notes/highlights section
(typically one document per book).

### The check that gates Phase 3B

**Do the exported annotations carry creation timestamps?** This is PRD open
question 1, and the answer changes what annotations are worth:

- **Yes** → every highlight is a direct observation: a known location, in a known
  book, on a known date. Worth more than every other reconstruction signal
  combined, and Phase 3B becomes the backbone of the timeline.
- **No** → annotations contribute content but contribute nothing to the timeline.

To check, open two or three of the annotation documents and look for a date on an
individual highlight — not just a date on the file or the export.

```fish
# Point this at wherever the notes actually landed
find ~/spine-data/takeout -iname "*note*" -o -iname "*highlight*" -o -iname "*annotation*" | head -20
```

Tell me what a single highlight entry looks like and I'll size Phase 3B off that.

### Known gap, expect it

Annotations only reach Drive if a book was **opened and a new note or highlight
added** after sync existed — per book. Historical highlights may simply be absent.
Do not treat the export as complete; the importer will report coverage rather than
assume it.

### Files

Play Books content is all user-uploaded and DRM-free, so the originals come back
intact. Per-book web download is the fallback for stragglers Takeout misses.

**Verify a sample byte-matches local copies** where those still exist — the ingest
pipeline must take originals, not Play Books' converted derivatives:

```fish
sha256sum ~/spine-data/takeout/path/to/book.epub /path/to/local/copy.epub
```

Then drop everything into `~/inbox` for the Phase 2 pipeline. Do not hand-rename
anything first.

---

## 3. Chrome download history (Phase 3C)

The hard lower bound on when a book entered the library, and the
highest-confidence signal after annotations.

**Quit Chrome first** — the file is locked while it runs.

```fish
cp ~/.config/google-chrome/Default/History ~/spine-data/chrome/History-laptop
# Brave:    ~/.config/BraveSoftware/Brave-Browser/Default/History
# Chromium: ~/.config/chromium/Default/History
```

Copy from **both machines** if books were downloaded on more than one, naming them
distinctly (`History-laptop`, `History-desktop`).

### Sanity check

```fish
sqlite3 ~/spine-data/chrome/History-laptop \
  "SELECT COUNT(*) FROM downloads WHERE target_path LIKE '%.epub' OR target_path LIKE '%.cbz' OR target_path LIKE '%.cbr' OR target_path LIKE '%.pdf';"
```

> **Gotcha the importer handles, worth knowing:** `downloads.start_time` is in
> Chrome epoch — microseconds since 1601-01-01 UTC, not unix seconds. The
> conversion is `unix = chrome_time / 1000000 - 11644473600`. A timeline that
> silently reads it as unix lands every book in 1601.

Android Chrome's history is not accessible without root and does not sync
downloads. If books were downloaded on the phone, those acquisition dates are
gone; filesystem mtimes are the fallback.

---

## 4. Play Books library ordering (Phase 3C upper bound)

Play Books orders the library by last-opened. That ordering is the only "last
touched" signal available, and it is an *ordering*, not dates — the importer turns
it into relative bounds, not timestamps.

1. <https://play.google.com/books> → **My books**
2. Sort by **Recent**
3. Capture the full list **in order**, top to bottom, into
   `~/spine-data/playbooks/library-order.txt` — one title per line, most recent
   first

Copy-paste or screenshots both work; a text file is easier for me to consume. Do
this before you stop opening books in the app, since opening one reorders it.

Also worth capturing while you are there: anything the app shows as a percentage
or "finished" state.

---

## 5. KOReader statistics (Phase 3E — the standing one)

The only source of **measured** reading time. The kosync protocol does not carry
it, so it is collected rather than synced.

Each device keeps its own `statistics.sqlite3`, and `book.md5` in it is the same
partial MD5 the sync protocol uses — which is what lets sessions join to
documents.

### Where it lives on-device

```
<KOReader install dir>/settings/statistics.sqlite3
```

On Android that is usually `/sdcard/koreader/settings/statistics.sqlite3`.

### Option A — one-off pull over USB (do this now, to verify)

```fish
mkdir -p ~/spine-data/koreader-stats/pixel
adb pull /sdcard/koreader/settings/statistics.sqlite3 ~/spine-data/koreader-stats/pixel/
```

Repeat per device into `koreader-stats/tab-s11/` and `koreader-stats/tab-2/`. Keep
the directory name stable per device — the importer uses it as `device_id`, and
renaming it later forks that device's session history.

### Option B — Syncthing (the standing setup)

Share the KOReader `settings/` directory from each device to
`~/spine-data/koreader-stats/<device>/` on the laptop, **send-only from the
device** so nothing the laptop does can write back into a live reader's settings.

Syncthing is installed. Start it and set up the shares:

```fish
systemctl --user enable --now syncthing.service
```

Then open <http://127.0.0.1:8384> and add each device.

### Verify a pull worked

```fish
sqlite3 ~/spine-data/koreader-stats/pixel/statistics.sqlite3 \
  "SELECT COUNT(*) FROM book; SELECT COUNT(*) FROM page_stat_data;"
```

Both should be non-zero if you have read anything on that device.

### This one doubles as a correctness check

`book.md5` is KOReader's own partial MD5, computed on-device. Once you have both a
stats database and files in the library, `kostats verify-md5` compares the
pipeline's ported implementation against KOReader's real output for every book —
which is a far stronger check than any test vector I could write.

---

## What to tell me when you are done

1. **Wellbeing:** whether the daily bucket exists and Play Books appears in it
2. **Takeout:** what a single highlight entry looks like — specifically whether it
   has a date on it
3. **Chrome:** the count from the sanity check above
4. **KOReader stats:** the two counts, per device

Items 1 and 2 close PRD open questions 2 and 1 respectively, which is what decides
whether Phase 3B and 3D get built at all.
