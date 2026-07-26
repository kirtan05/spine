# spine — PRD

A self-hosted reading stack: a kosync-compatible progress server on Cloudflare, a
local ingest pipeline, a Play Books importer, and a public reading page.

**Status:** reviewed draft for implementation (v2)
**Owner:** Kirtan Jain
**Target host:** kirtanjain.com (Cloudflare)

---

## 1. Problem

Reading is split across Google Play Books (ebooks), ad-hoc comic readers, and nothing
at all for tracking. Three specific failures:

1. **No history.** Play Books does not expose time-based reading stats, a reading
   journal, or a list of what was finished when.
2. **No unified library.** Ebooks and comics live in different places, with
   inconsistent metadata and no series grouping.
3. **No offline-tolerant sync.** Reading happens on a Pixel and two tablets, often
   with no network. Any design that requires a home server to be reachable at read
   time is unusable.

## 2. Approach

Split responsibilities by data shape rather than by device:

| Data | Property | Where it lives |
|---|---|---|
| Reading position | Unmergeable scalar, needs always-on | Cloudflare Worker + D1 |
| Reading sessions | Append-only per device, merges trivially | Same D1, imported by the laptop from each device's KOReader statistics database (Phase 3E) |
| Library files | Large, static once published | Laptop, served by Kavita |
| Metadata | Curated, changes rarely | Normalized at ingest, then frozen |

The Worker is the only always-on component. Everything else may be offline
indefinitely without blocking reading.

Note the sessions row carefully: **nothing in the kosync protocol carries sessions.**
The protocol syncs position only. Measured reading time exists solely in each device's
local `statistics.sqlite3`, so session data is *collected* (Phase 3E), not synced.

## 3. Non-goals

Explicitly out of scope. Do not implement these even if they seem adjacent:

- **DRM circumvention.** All source files are user-uploaded and DRM-free. No handling
  of Adobe DRM, no `.acsm`, no decryption.
- **Search-history mining.** Evaluated and rejected: sparse coverage, bidirectional
  noise, high entity-matching cost, low yield.
- **Recovering pre-Wellbeing durations.** That data does not exist. The system records
  timeline without duration for that period and does not estimate.
- **A native Android client.** KOReader is the reader on all mobile devices.
- **Multi-user.** Single user, single account. No sharing, no roles. (Accepted
  consequence: `progress` rows are not scoped to a username. Fine at this scale.)

## 4. Phases

Each phase ships independently and is useful on its own.

---

### Phase 1 — `spine`: kosync-compatible sync server

**Goal:** KOReader on three devices syncs reading position through kirtanjain.com,
with no dependency on the laptop.

**Platform.** A standalone Worker bound to **routes** on the existing zone —
`kirtanjain.com/users/*`, `kirtanjain.com/syncs/*`, and later `kirtanjain.com/api/*` —
with a D1 binding and (Phase 4) a Cron Trigger. The static site stays on Pages,
untouched: Workers routes execute on proxied hostnames in front of whatever serves
them, and take precedence when configured on the same hostname. So there is no
Pages-to-Workers migration and no domain switchover. Verify precedence once with a
throwaway route (e.g. `kirtanjain.com/spine-canary/*`) before pointing any device at
it. Cron Triggers attach to this Worker directly; the earlier assumption that the
whole site had to move to Workers for cron was wrong.

**Protocol.** Implement the stock kosync API so KOReader needs only a URL change.
Verified against the plugin's `api.json` in the KOReader tree:

| Method | Path | Notes |
|---|---|---|
| POST | `/users/create` | Client expects 201 on success, 402 on conflict. Open only while `users` is empty; `ALLOW_REGISTRATION` is a re-open override, not the primary gate |
| GET | `/users/auth` | 200 `{"authorized":"OK"}` on valid credentials, 401 otherwise |
| PUT | `/syncs/progress` | Body: `document`, `progress`, `percentage`, `device`, `device_id`, and optional `metadata` `{filename, title, authors}` — sent by stock KOReader only when its **Send document metadata** setting is enabled (off by default; enable on every device). Client accepts 200/202. Unknown fields are ignored |
| GET | `/syncs/progress/:document` | Returns the resolved position, including the winning row's `device`, `device_id`, and `timestamp` — the client uses these to decide whether to prompt |

Status codes are part of the contract: the plugin switches behaviour on 200/201/202/
401/402, and the wrong code reads as an auth failure on-device. Test against a real
device, not assumptions.

When `metadata` arrives on a push, upsert `documents` from it. This is the primary
title source for Phase 4; the Phase 2 catalogue sync is the enrichment path (series
data, and comics whose display titles come from ComicInfo).

**Auth.** `X-AUTH-USER` plus `X-AUTH-KEY` (MD5 of password), with
`Accept: application/vnd.koreader.v1+json`. Weak, inherited from the protocol, not a
design choice. Mitigations, in order of what actually matters:

- **Require a random credential used nowhere else.** This is the real control;
  everything below is secondary.
- Store `HMAC-SHA-256(AUTH_PEPPER, key)` via WebCrypto, never the key itself; compare
  in constant time. **Not bcrypt or argon2:** a memory-hard KDF on every request
  exceeds the Workers free-tier CPU budget (~10 ms), pure-JS bcrypt especially, and it
  would defend a credential the wire format already caps at unsalted MD5. If the plan
  ever moves to paid Workers *and* the unique-credential rule is broken, upgrade to
  PBKDF2 via WebCrypto.
- Rate limit `/users/*` with a Cloudflare WAF rate-limiting rule (the free plan
  includes one). Do **not** implement rate limiting as D1 counters — that converts an
  attack into a write-quota bill.
- `updated_at` on progress rows is assigned **server-side**. Device clocks skew, and
  the `newest` policy must not depend on them.

**Schema (D1):**

```sql
-- All timestamps are unix seconds. Server-assigned unless noted otherwise.

CREATE TABLE users (
  username    TEXT PRIMARY KEY,
  key_hash    TEXT NOT NULL,      -- HMAC-SHA-256(AUTH_PEPPER, client MD5 key)
  created_at  INTEGER NOT NULL
);

-- doc_hash is KOReader's "binary" document id: MD5 over 1 KiB samples at
-- exponentially spaced offsets (partialMD5 in KOReader's frontend/util.lua:
-- seek to 1024 << 2i for i = -1..10, read 1 KiB each, stop at EOF).
-- The ingest pipeline ports that exact loop; do not reimplement from a description.
CREATE TABLE documents (
  doc_hash      TEXT PRIMARY KEY,
  title         TEXT,
  authors       TEXT,
  series        TEXT,
  series_index  REAL,
  filename      TEXT,
  first_seen    INTEGER NOT NULL
);

CREATE TABLE progress (
  doc_hash    TEXT NOT NULL,
  device_id   TEXT NOT NULL,
  device      TEXT,
  progress    TEXT NOT NULL,      -- page number or xpointer, opaque
  percentage  REAL NOT NULL,
  updated_at  INTEGER NOT NULL,   -- server clock, never the device's
  PRIMARY KEY (doc_hash, device_id)
);

CREATE TABLE sessions (
  id           TEXT PRIMARY KEY,  -- deterministic: hash(source, device_id, doc_hash, started_at)
  doc_hash     TEXT,
  title        TEXT,
  authors      TEXT,
  device_id    TEXT,
  started_at   INTEGER NOT NULL,
  ended_at     INTEGER,           -- interval rows set this; point rows leave NULL
  duration_s   INTEGER,
  pages        INTEGER,
  source       TEXT NOT NULL,     -- koreader | kavita | playbooks | wellbeing
  confidence   TEXT NOT NULL,     -- exact | approx | inferred
  method       TEXT,              -- e.g. interval, wellbeing-proportional/monthly
  imported_at  INTEGER NOT NULL
);

CREATE INDEX idx_sessions_started ON sessions(started_at);
CREATE INDEX idx_sessions_doc ON sessions(doc_hash);
```

Two deliberate changes from the first draft: `ended_at` exists so Phase 3C interval
rows can be stored faithfully instead of losing their upper bound, and session ids are
**deterministic** rather than client-generated UUIDs — random UUIDs and "re-running an
importer produces no duplicates" contradict each other unless the id is derived from
the natural key.

**Conflict policy.** `progress` stores one row per `(doc_hash, device_id)`. Resolution
on read is a pure function over those rows, selected by a `CONFLICT_POLICY` env var:

- `furthest` — highest percentage wins. Safer for linear reading.
- `newest` — most recent `updated_at` wins. Correct for re-reads and skipping back.

Default to `newest`. Storing per-device rows rather than one row per document is what
makes this switchable later and makes ping-pong debuggable. The resolved response
returns the winning row's `device`/`device_id`/`timestamp` intact.

**Backups.** From day one, not from Phase 4: a systemd timer (`Persistent=true`) on
the laptop runs `wrangler d1 export --remote` weekly and before any schema change.
The history is the product; D1 Time Travel is a convenience, not a backup.

**Acceptance criteria:**

- KOReader on Pixel, Tab S11, and tablet 2 all register and authenticate
- Document matching set to Binary and **Send document metadata** enabled on all three;
  push and pull both succeed, and a pushed title appears in `documents`
- Pull returns the correct position after a push from a different device
- Progress written while all three are offline reconciles correctly on reconnect
- A push from a device with a deliberately wrong clock still resolves correctly under
  `newest` (server-side timestamps proven, not assumed)
- Registration closes itself after the first user; unauthenticated requests return 401
- Response bodies and status codes match the plugin's `api.json`, verified on-device
- Auth verification stays inside the free-tier CPU budget (check `wrangler tail`)
- A D1 export restores into a fresh database and the Worker serves from it

**Known protocol behaviour to document, not fix:** the client pushes on document close
and device suspend, debounced to 25 s. A book left open on an awake device will not
propagate until one of those happens.

---

### Phase 2 — `shelf`: local ingest pipeline

**Goal:** A file dropped in a watch folder ends up correctly named, tagged, and
placed in the Kavita library, or in quarantine with a reason.

**Runs on:** the EndeavourOS laptop. Python, invoked by a systemd path unit.

**Pipeline:**

1. **Watch** — systemd path unit on `~/inbox`. Prefer reacting to close-for-write
   (`IN_CLOSE_WRITE`) over a time debounce; keep a size-stable check as fallback for
   multi-part downloads
2. **Identify** — magic bytes first, extension second: plenty of `.cbr` files are
   ZIPs and vice versa. Route on what the file *is*
3. **Convert** — RAR-family to CBZ with `unar` (libarchive/bsdtar's RAR5 support is
   unreliable); repack with `zip -0`, no image recompression
4. **Spread handling** — optional KCC pass to pre-rotate two-page spreads, since
   KOReader has no auto-rotate for these. Two hard rules: **one canonical output for
   all devices** — a per-device render changes the bytes, which changes the partial
   MD5, which forks progress per device, the exact failure this system exists to
   prevent — and **archive the pre-KCC original** in cold storage, because the pass is
   lossy and device lineups change
5. **Tag** — `ebook-meta` for EPUB (author, series, series index); `comictagger` for
   comics (ComicInfo.xml)
6. **Name** — `{Author}/{Series}/{Series} {NN} - {Title}.{ext}`, matching Kavita's
   series-detection patterns
7. **Publish** — atomic move into the Kavita library root. Kavita must be configured
   to never write metadata back into files (leave write-back features off; if ever
   swapping to Komga, make the same check) — a library server that "helpfully"
   updates ComicInfo.xml changes hashes
8. **Record** — append a row to a local SQLite ledger: full content hash, KOReader
   partial MD5, metadata, destination
9. **Sync catalogue** — upsert `(doc_hash → title, authors, series, series_index)`
   into the D1 `documents` table, using a Cloudflare API token scoped to D1 (via
   `wrangler d1 execute` or the D1 HTTP API). The Worker exposes **no** admin
   surface; all write access beyond kosync lives in a laptop-side credential

**Hard invariant:** published files are immutable. KOReader's Binary matching keys on
a partial MD5 of file contents, so re-tagging or repacking a file after it has been
downloaded to a device forks it into a separate book with separate progress.
Normalization happens before publish, never after.

**Quarantine:** anything with unparseable metadata, an ambiguous series match, or a
name collision goes to `~/quarantine/{reason}/`, suffixed with the content hash to
avoid collisions inside quarantine, and is never guessed at.

**Idempotency:** keyed on content hash. Re-running over the same input is a no-op.

**Acceptance criteria:**

- 50 mixed EPUB and CBR files process without manual intervention
- Kavita groups them into the expected series with no orphans
- Re-running the pipeline over the same inputs produces zero writes
- A file with no usable metadata lands in quarantine rather than the library
- No published file's hash changes across two pipeline runs
- The pipeline's computed partial MD5 matches what KOReader reports for the same file
  (compare against a real device once, then keep a reference vector in the tests)
- Every published file has a `documents` row in D1 before it is first opened on a
  device

---

### Phase 3 — `allebooks`: Play Books port and timeline reconstruction

**Goal:** Recover the library files and as much reading history as honestly exists.

**Part A — files.** All Play Books content is user-uploaded and DRM-free. Retrieve
the originals in one shot via Google Takeout (per-book web download is a fallback for
stragglers) and feed them into the Phase 2 pipeline. Verify a sample of retrieved
files byte-matches local copies where those still exist — the pipeline must ingest
originals, not converted derivatives.

**Part B — annotations.** Export via Takeout (Play Books section: notes, highlights,
bookmarks, one document per book).

Check first, before building anything else: **do the exported annotations carry
creation timestamps?** If yes, each highlight is a direct observation — a known
location in a known book on a known date — and is worth more than every other signal
combined. If no, annotations contribute content but not timeline.

Also note the sync gotcha: annotations do not reach Drive unless the book is opened
and a new note or highlight is added, per book. Historical highlights may simply be
absent. Do not assume completeness.

**Part C — timeline.** Build an interval per book from:

- **Acquisition** — Chrome's `History` SQLite `downloads` table (`start_time`), plus
  filesystem mtimes on both machines. Hard lower bound. Highest-confidence signal
  after annotations.
- **Last touched** — Play Books library ordering reflects last-opened. Upper bound.

The interval `[acquired, last_opened]` is the core output, written as `sessions` rows
with `started_at = acquired`, `ended_at = last_opened`, `duration_s = NULL`,
`source = 'playbooks'`, `confidence = 'inferred'`, `method = 'interval'`.

**Part D — duration allocation, Wellbeing window only.** Export with
`adb shell dumpsys usagestats` on the Pixel. Retention is per bucket, not one number:
daily stats survive roughly a week, weekly about a month, monthly about six months,
yearly about two years. Capture every bucket in a single export, allocate at the
finest granularity available for each period, and encode the bucket in the method —
`wellbeing-proportional/daily` and `wellbeing-proportional/monthly` are different
models with very different error bars, and the column should say which one produced a
row.

For each period with Play Books foreground time, allocate across the books whose
intervals overlap it, weighted by remaining page count. Caveats to record in the
output, not just code comments: foreground time conflates reading with browsing;
there is no per-book attribution in the source; coarse buckets smear time across
everything open that month.

One-off script, not a standing pipeline, unless retention turns out better than
documented.

**Part E — KOReader statistics import (standing).** KOReader's statistics plugin
records per-page reading time in a local `statistics.sqlite3` on each device
(`book(id, title, authors, md5, …)` and `page_stat_data(id_book, page, start_time,
duration)`), and `book.md5` is the same partial MD5 the sync protocol uses. This is
the only source of **measured** sessions, and the kosync protocol does not carry it —
so it is collected, not synced:

- Syncthing (or periodic USB copy) mirrors each device's `statistics.sqlite3` to the
  laptop
- An importer opens each copy read-only, groups contiguous `page_stat_data` rows into
  sessions (gap threshold ~5 min), maps `book.md5 → doc_hash`, and writes rows with
  `source = 'koreader'`, `confidence = 'exact'`, `device_id` set
- Ids are deterministic — `hash(source, device_id, doc_hash, started_at)` — so
  re-runs are no-ops by construction

This resolves former open question 3: `spine` absorbs statistics ingestion directly,
and KoInsight is not used (running both would duplicate rows).

**The honesty constraint.** Every aggregate query in Phase 4 must be able to filter to
`confidence = 'exact'` and still return something meaningful. Modelled rows carry a
`method` so a future reader can tell which model produced a number. No inferred row is
ever written without both `confidence` and `method` set.

**Acceptance criteria:**

- All Play Books uploads retrieved and ingested through Phase 2
- Annotations imported, with a report on how many books had any
- A timeline covering the library, with explicit gaps where evidence is absent
- `SELECT SUM(duration_s) FROM sessions WHERE confidence='exact'` returns only
  measured time
- Re-running any importer twice produces no duplicate rows (deterministic ids)
- A week of normal reading on all three devices imports as sessions whose per-device
  totals match KOReader's own on-device statistics screen

---

### Phase 4 — `/reading` page and stats rollup

**Goal:** A public page on kirtanjain.com showing current and recent reading, driven
by real sync traffic.

**Titles.** From the `documents` table — fed by KOReader's metadata push (Phase 1)
and the pipeline catalogue sync (Phase 2, step 9). A hash with no catalogue row
renders as "Unknown" and is a signal that a step was missed, not something to paper
over.

**Finished.** The protocol has no "finished" event, so define one in the rollup: a
book is finished when its resolved percentage first crosses `FINISHED_THRESHOLD`
(default 0.98, configurable — comics with end-matter and books with long appendices
sit differently). Store the crossing date.

**Rollup.** A Cron Trigger aggregates `sessions` nightly into a summary table — daily
totals, per-book totals, streaks, re-read counts. Split by `confidence` so measured
and modelled numbers are never summed into one figure without a flag. Daily buckets
use **Asia/Kolkata** dates, not UTC — a stats page that splits days at 05:30 local is
wrong forever after. Schedule the cron after local midnight, e.g. `30 19 * * *` UTC
(01:00 IST), so "yesterday" is complete when it rolls up.

**Page.** Currently-reading with percentage, recently-finished, and a stats view,
served as a static page fetching a public JSON route on the Worker
(`/api/reading`). Set `Cache-Control: public, s-maxage=3600` on that route so page
views hit the edge cache, not D1.

**Privacy.** The page is public; the sync endpoint is not. Do not expose `device_id`,
raw progress strings, or anything from `users`. Default decision (revisit after
launch): the public page shows currently-reading plus a 90-day recent window; full
history is not rendered on any public route.

**Acceptance criteria:**

- Closing a book on any device updates the page within one rollup cycle
- Every displayed aggregate is labelled measured or estimated
- The page renders correctly with an empty database
- No authenticated data is reachable from a public route
- Daily totals bucket on IST dates (verify with a session written at 23:30 IST)

---

## 5. Open questions

1. Do Play Books annotation exports carry timestamps? Blocks Phase 3B scoping.
2. Actual `usagestats` retention per bucket on this specific Pixel — determines
   whether Phase 3D is worth even the one-off script.
3. ~~KoInsight vs direct stats ingestion~~ — **resolved:** `spine` absorbs statistics
   ingestion via the Phase 3E importer; KoInsight is out.
4. ~~Pages-to-Workers domain switchover~~ — **resolved:** no migration. The Worker
   runs on routes in front of the existing Pages site; cron attaches to that Worker.
5. ~~Should `/reading` be public?~~ — **resolved as a default:** public, limited to
   currently-reading plus a 90-day window. Revisit after living with it.

## 6. Sequencing note

Phase 1 is the only phase with a hard dependency for daily use. Phases 2 and 3 improve
the library but nothing breaks without them. Phase 4 is cosmetic. Ship Phase 1 **with
the backup timer running from day one**, live on it for two weeks, and let real usage
inform whether the conflict policy default was right before building further.
