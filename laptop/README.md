# laptop

The sometimes-on half of spine. Nothing here has to be running for reading to
work — that is the whole point of the split.

Two commands, one shared core:

| Package | What |
|---|---|
| `shelf` | Phase 2 ingest pipeline: inbox → normalised → Kavita library → D1 catalogue |
| `kostats` | Phase 3E importer: each device's `statistics.sqlite3` → measured sessions in D1 |
| `spinecore` | The ported KOReader hash, the D1 HTTP client, configuration |

## Setup

```fish
uv sync
cp .env.example .env   # fill in the D1 credentials — DEPLOY.md step 10
```

External tools, all already installed:
`unar` (RAR), `zip`/`7z`, `ebook-meta` (calibre), `comictagger`. `kcc-c2e` is
optional and only needed if the spread pass is turned on.

## shelf

```fish
uv run shelf ingest              # drain ~/inbox
uv run shelf ingest path/to/file # one file
uv run shelf status              # counts, paths, outstanding catalogue rows
uv run shelf sync-catalogue      # push pending documents rows to D1
uv run shelf hash FILE           # KOReader's doc_hash for a file
```

Normally invoked by `shelf-ingest.path` when something lands in `~/inbox`.

### The invariant

**Published files are immutable.** KOReader identifies a book by a partial MD5 of
its contents, so re-tagging or repacking a file after a device has downloaded it
forks it into a separate book with separate progress — and the history already
recorded against the old hash is orphaned.

Everything the pipeline does happens before publish. Nothing touches a file
afterwards. Kavita must also be configured never to write metadata back into
files, or it will do this to you on its own.

Two consequences worth knowing:

- Comics are **repacked deterministically** — stored, never recompressed, at a
  pinned timestamp and a sorted entry order. `zip -0` embeds real mtimes, so two
  runs over the same input would produce two different books.
- EPUBs **pass through untouched**. Writing tags into them would change their
  identity for no gain; Kavita reads the OPF and D1 carries the rest.

### Quarantine

Unparseable metadata, an ambiguous series match, or a name collision sends a file
to `~/quarantine/{reason}/`, suffixed with its content hash. Nothing is guessed
at: a wrong series assignment silently merges two series in Kavita and leaves no
trace that it happened.

### Idempotency

Keyed on the source content hash. Re-running over the same input reads the ledger
and stops — no conversion, no writes.

## kostats

```fish
uv run kostats devices           # which statistics databases were found
uv run kostats import --dry-run  # report without writing
uv run kostats import            # write sessions to D1
uv run kostats verify-md5        # check the ported hash against KOReader's own
```

Sessions land with `source='koreader'`, `confidence='exact'` — the only measured
rows in the system. Ids are derived from
`(source, device_id, doc_hash, started_at)`, so re-running is a no-op by
construction rather than by a de-duplication pass.

`duration_s` is the sum of measured page durations, not wall-clock span: ten
minutes read, four idle, ten more is twenty minutes of reading. `ended_at` keeps
the span so nothing is lost.

### verify-md5 is the real test

Test vectors can only check the port against itself. `book.md5` in a device's
statistics database was computed by KOReader, on a device, over the real file. If
the port were wrong, nothing would match.

It also catches the opposite failure: a published file whose hash has drifted
since ingest, which means the immutability invariant has been broken somewhere.

## Tests

```fish
uv run pytest
uv run ruff check src tests
```

Tests never touch the real Cloudflare account — the fixtures strip the
credentials from the environment.
