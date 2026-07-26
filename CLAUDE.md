# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A self-hosted reading stack, specified in `PRD.md`. Read that before changing
behaviour — most decisions here have a stated reason, and several of them are
counter-intuitive.

## Commands

### Worker (`worker/`)

```fish
npm test                              # 57 tests against real D1 in workerd
npx vitest run test/sync.test.ts      # one file
npx vitest run -t "clamps a percentage"   # one test by name
npm run typecheck                     # regenerates types, then tsc --noEmit
npm run deploy
npm run migrate:remote                # after adding a migration
npm run backup                        # wrangler d1 export --remote
npx wrangler tail                     # live logs
```

`npm run types` (`wrangler types`) regenerates `worker-configuration.d.ts`, which is
gitignored. Run it after any `wrangler.jsonc` change or `Env` will be stale.

### Laptop (`laptop/`)

```fish
uv run pytest                         # 71 tests
uv run pytest tests/test_pipeline.py::TestIdempotency -v
uv run ruff check src tests
uv run shelf ingest                   # drain ~/inbox
uv run shelf status
uv run kostats import --dry-run
uv run kostats verify-md5             # port vs KOReader's own hashes
```

## Architecture

Three deployables, split by **how often they can be offline**, not by domain:

| Path | Runs | Holds |
|---|---|---|
| `worker/` | Cloudflare, always | kosync protocol, D1, `/api/reading`, nightly rollup |
| `laptop/` | Sometimes | `shelf` ingest pipeline, `kostats` importer, `spinecore` shared |
| `site/reading/` | Static | The public page, dropped into the existing Pages site |

Reading position is an unmergeable scalar that must be reachable the moment a book
opens — often on a train, with the laptop asleep. Library files are large and
static. Those two shapes want different homes, and that split is the entire design.

The Worker runs on **routes** in front of the existing Pages site
(`kirtanjain.com/users/*`, `/syncs/*`, `/api/*`). There is no Pages-to-Workers
migration; Workers routes take precedence on a proxied hostname.
`/spine-canary/ping` exists to prove that and is safe to delete.

## Things that will bite you

**`partial_md5` is the identity of every book.** `laptop/src/spinecore/partial_md5.py`
is a port of KOReader's `util.partialMD5`. The first sample offset is **0**, not 256:
the Lua uses LuaJIT's `bit.lshift`, which masks the shift count to five bits, so
`lshift(1024, -2)` is a left shift by 30 truncated to 32 bits. A port that samples at
256 produces a plausible hash matching nothing KOReader ever computed. Do not
"simplify" the shift arithmetic. `kostats verify-md5` checks it against real
on-device hashes.

**Push must return exactly 200.** `api.json` lists `[200, 202, 401]`, but
`KOSyncClient.lua:137` reports success as `res.status == 200`. A 202 is a silent
failure that gets queued for retry forever. Likewise, a refusal the reader needs to
read must be **402**, not 403/409 — Spore raises a Lua error for any status outside
`api.json`'s expected list, which shows on-device as an unexplained error with no
message.

**`updated_at` is always the server clock.** The protocol carries no client
timestamp. Never accept one.

**`confidence` is in the primary key of every aggregate table.** There is no row
shape that can hold measured and modelled time summed together, so no query can
produce one by accident. Keep it that way when adding aggregates.

**Daily buckets are Asia/Kolkata dates** (`worker/src/time.ts`), never UTC. IST has
no DST, so the fixed +19800 offset is exact rather than an approximation.

**Published files are immutable.** Everything the pipeline does happens before the
file lands in the library. Comics are repacked deterministically — pinned timestamp,
sorted entries, `ZIP_STORED` — because `zip -0` embeds real mtimes and would produce
a different book on every run. EPUBs pass through untouched.

**Catalogue precedence differs by direction.** A KOReader metadata push only fills
NULLs (`worker/src/documents.ts`); the pipeline's catalogue sync overwrites
(`laptop/src/spinecore/d1.py`). Otherwise a 25-second metadata push would clobber a
curated ComicInfo title on every sync.

**The metadata push does not exist in shipping KOReader.** Verified against the
v2026.03 plugin extracted from the APK: no `getMetadata`, no `send_metadata`, and
`api.json`'s `update_progress` payload is only
`document, progress, percentage, device, device_id`. It is a master-branch feature.
So the PRD's "primary title source" is currently the *pipeline*, and `documents`
stays empty for anything not published through `shelf`. The Worker's handling is
already correct and forward-compatible — leave it.

**`auto_sync` defaults to false and is the setting that matters.** With it off,
`registerEvents()` sets `onCloseDocument`/`onSuspend` to `nil` and `onReaderReady`
skips the pull, so nothing syncs and nothing errors. If a device "isn't syncing"
and the server has *zero* rows, check that before anything else — a device that
pushes and is rejected leaves traces, one that never pushes leaves none.

**`kostats` snapshots the WAL sidecars** before reading `statistics.sqlite3`.
Opening a synced copy read-only without its `-wal` returns data as of the last
checkpoint — a successful-looking import quietly missing the newest reading.

## Worker specifics

- Conflict resolution (`src/conflict.ts`) is a pure function over per-device rows.
  `progress` keeps one row per `(doc_hash, device_id)` on purpose: it makes the
  policy switchable after the fact and keeps ping-pong debuggable.
- Finished detection runs on **push**, not in the rollup — a crossing that reverses
  before the nightly cron would never be seen by a daily snapshot.
- Tests truncate every table in `test/setup.ts` `beforeEach`. vitest-pool-workers
  v0.18 dropped automatic per-test storage isolation; without this, rows leak
  between tests.
- `compatibility_date` must not exceed the bundled workerd's build date or local
  dev and tests fail with `ERR_FUTURE_COMPATIBILITY_DATE`.
- v0.18 has no `defineWorkersConfig`; config uses the `cloudflareTest()` Vite plugin.

## Secrets and D1 access

`AUTH_PEPPER` lives in `wrangler secret` (and `.dev.vars` locally). The scoped D1
token lives in `laptop/.env`. Both are gitignored. The Worker exposes no admin
surface by design — every write beyond kosync comes from the laptop.

`spinecore.d1.get_client()` picks one of two transports: the REST API when
`CF_API_TOKEN` is set, otherwise `wrangler d1 execute` reusing the `wrangler
login` OAuth session. The wrangler path uses `--command`, **not** `--file`: the
file form prints a run summary instead of rows, so a SELECT through it silently
returns query statistics rather than data. It also has no parameter binding, so
values are inlined by `sql_literal`/`inline_params` — that pair is the security
boundary for the fallback and is tested directly in `tests/test_d1.py`.

## The public page

Two implementations, deliberately:

- `site/reading/` — framework-free, self-contained, for anyone using this repo.
- `kirtansite/src/pages/reading.astro` — the one actually deployed, written in
  that site's design system. Its `<style>` must stay `is:global`, because Astro's
  scoping never matches the elements the script creates at runtime.

Changing the `/api/reading` response shape means updating both.

## Scope

`PRD.md` §3 lists explicit non-goals: no DRM handling, no search-history mining, no
estimating pre-Wellbeing durations, no native Android client, no multi-user. Phases
3A–3D (Play Books files, annotations, timeline, Wellbeing allocation) are **not
built** — they need real Takeout and `adb` exports first. See
`docs/DATA-COLLECTION.md`.
