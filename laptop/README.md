# laptop

The sometimes-on half of spine. Nothing here has to be running for reading to
work — that is the whole point of the split.

| Package | What |
|---|---|
| `shelf` | Ingest: inbox → curated metadata → `~/library` (→ Syncthing → devices) → D1 catalogue. Plus `reconcile`, which brings the whole library back into line with the catalogue |
| `kostats` | Each reader's statistics database (Readest `statistics.db`, KOReader `statistics.sqlite3`) → measured sessions in D1 |
| `spinecore` | The ported KOReader hash, the D1 clients, configuration |
| `mappings/` | The curated catalogue: author aliases, per-book fixes, series orders |

## Setup

```fish
uv sync
cp .env.example .env   # optional: a scoped D1 token — DEPLOY.md step 10
```

Without a token, D1 is reached through the `wrangler login` session. External
tools: `unar` (RAR), `zip`/`7z`, `ebook-meta` (calibre), `comictagger`; `kcc-c2e`
only if the spread pass is on.

## shelf

```fish
uv run shelf ingest              # drain ~/inbox (and ~/inbox/phone)
uv run shelf ingest path/to/file # one file
uv run shelf status              # counts, paths, outstanding catalogue rows
uv run shelf reconcile           # dry run: what the catalogue would change
uv run shelf reconcile --apply   # rewrite, move, and migrate hash-keyed rows
uv run shelf sync-catalogue      # push pending documents rows to D1
uv run shelf retry-quarantine    # give quarantined files another pass
uv run shelf hash FILE           # the doc_hash of a file
```

Normally invoked by `shelf-ingest.path` when something lands in `~/inbox` or
`~/inbox/phone` (the phone's downloads, via Syncthing).

### The catalogue

Readest groups by the author and series *inside* each EPUB and ignores folders, so
filing a book correctly on disk is not enough. `mappings/` holds what the files
themselves get wrong:

| File | Holds |
|---|---|
| `authors.json` | exact author strings → one canonical spelling (`Le Guin, Ursula K.` → `Ursula K. Le Guin`) |
| `books.json` | per-book fixes, keyed by the ingested filename, for what no rule should guess (title and author swapped, no author at all) |
| anything else | series orders: `{author, series, titles: {canonical title: number}}` |

Series titles match by whole-word containment within one author, so `Cradle 2:
Soulsmith`, `03. Blackflame` and `[Liz Carlyle 04] • Liz Carlyle - 04 - Dead Line`
all resolve. A second candidate is refused, not guessed at. A bare retail suffix
(`The Maid: A Novel`) is dropped; a series subtitle (`A Crossfire Novel`) is not.

Ingest applies the catalogue before publishing, so a new book is written once with
its final metadata — and a second edition of an owned book resolves to the same
path and is quarantined as a collision rather than published beside it.

### The invariant

A book's identity is a partial MD5 of its contents; positions, sessions and
finishes are all keyed on it. So nothing rewrites a published file except
`reconcile`, and reconcile does it properly:

1. reads what the EPUB itself says (through a `.epub`-named symlink — see below);
2. applies the catalogue;
3. rewrites the file only if it disagrees, and reads it back to verify the write
   took (calibre reports success for writes it skips: it cannot clear an EPUB 3
   `belongs-to-collection`);
4. moves it to `Author/Series/Series NN - Title.epub`;
5. moves every hash-keyed D1 row to the new hash and updates the ledger.

A second run plans nothing. Back up first (`systemd/backup.sh`); it is a bulk
operation on the only irreplaceable data.

Comics are **repacked deterministically** — stored, never recompressed, at a pinned
timestamp and a sorted entry order. `zip -0` embeds real mtimes, so two runs over
the same input would produce two different books.

### Google Takeout

- Most EPUBs are named `.pdf`. `ebook-meta` picks its reader by extension and, read
  as a PDF, returns the filename as the title and "Unknown" as the author. Anything
  not called `.epub` is read through a `.epub`-named symlink.
- Some EPUBs were zipped together with their folder, so there is no
  `META-INF/container.xml` at the root and no reader can open them. Ingest repacks
  these, deterministically, before anything reads them.

### Quarantine

Unreadable metadata, an ambiguous catalogue match, or a name collision (usually
another edition of a book you own) sends a file to `~/quarantine/{reason}/`. Nothing
is guessed at.

### Idempotency

Keyed on the source content hash. Re-running over the same input reads the ledger
and stops — no conversion, no writes. Files already ingested are removed from the
inbox as skipped, so re-syncing a phone's whole Download folder costs nothing.

## kostats

```fish
uv run kostats devices           # which statistics databases were found
uv run kostats import --dry-run  # report without writing
uv run kostats import            # write sessions to D1
uv run kostats verify-md5        # check the ported hash against KOReader's own
```

Databases are found under `~/spine-data/koreader-stats/<device>/`, and the
directory name is the device id:

| Directory | Fed by |
|---|---|
| `<name>-readest/statistics.db` | Readest on a device, via Syncthing from `Books/Readest` |
| `laptop-readest/statistics.db` | Readest on this laptop — symlinks into `~/.local/share/com.bilingify.readest/Readest/` |
| `<name>/statistics.sqlite3` | KOReader, via Syncthing or the adb fallback in `collect-stats.sh` |

Readest writes KOReader's statistics schema, keyed by the same partialMD5; the
filename decides the session's `source`. One directory holding both is refused, and
hidden directories (Syncthing's `.stversions/`) are skipped.

Sessions land with `confidence='exact'` — the only measured rows in the system.
Ids are derived from `(source, device_id, doc_hash, started_at)`, so re-running is
a no-op by construction.

**`KOSTATS_SINCE`** (in `.env`, a date read as IST midnight) is the only way to
reset stats. Devices keep their databases forever and the import is idempotent, so
rows deleted from D1 come back the next night; the cutoff stops them at the source.

`duration_s` is the sum of measured page durations, not wall-clock span: ten
minutes read, four idle, ten more is twenty minutes of reading. `ended_at` keeps
the span so nothing is lost.

### verify-md5 is the real test

Test vectors can only check the port against itself. `book.md5` in a device's
statistics database was computed by the reader, on a device, over the real file.
If the port were wrong, nothing would match.

## Tests

```fish
uv run pytest
uv run ruff check src tests
```

Tests never touch the real Cloudflare account or your settings: `conftest.py` sets
`SPINE_D1=off` (unsetting the token is not enough — the wrangler OAuth fallback
would still reach production) and `SPINE_DOTENV=off` for every test. Calibre-backed
tests run through `spinecore.process.run`, because under `uv run` a bare subprocess
resolves `ebook-meta`'s `#!/usr/bin/env python3` to this venv's interpreter.
