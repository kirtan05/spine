# spine

A kosync-compatible reading-progress server that runs on Cloudflare Workers and D1.

Point KOReader at your own domain and your reading position syncs across every device,
with no server to keep alive at home.

```
Progress sync → Custom sync server → https://your-domain.com
```

That's the whole setup. `spine` implements the stock kosync protocol, so KOReader needs
a URL change and nothing else.

## Why

Most self-hosted reading setups put progress sync on the same box as the library. That
box is a laptop or a NAS, and when it's asleep your devices can't sync — which is
exactly when you're out reading on them.

Reading position is a single number. It can't be merged, only chosen. So it belongs on
whatever node you can keep up cheapest, separately from the large, sleepy thing holding
your files. If you already host a site on Cloudflare, you already have that node:
`spine` deploys as a standalone Worker on routes under your existing domain, and the
site itself doesn't move.

## Features

- Full stock kosync protocol — works with unmodified KOReader
- Per-device progress rows, so conflicts are visible rather than silently resolved
- Switchable conflict policy: `furthest` or `newest`
- D1 (SQLite) storage — queryable, unlike KV
- A `documents` catalogue, filled from KOReader's optional metadata push and/or your
  ingest pipeline, so hashes have titles
- Optional session history with explicit provenance (see below)
- Runs comfortably inside Cloudflare's free tier for a single reader

## Provenance, or: not lying to yourself

If you import reading history from elsewhere, `spine` requires every session row to
declare where it came from:

| Column | Values |
|---|---|
| `source` | `koreader`, `kavita`, `playbooks`, `wellbeing` |
| `confidence` | `exact`, `approx`, `inferred` |
| `method` | which model produced an inferred row |

Every aggregate can be filtered to `confidence = 'exact'`. This matters more than it
sounds: reconstructed history is seductive, and a lifetime-hours figure that silently
mixes measurement with estimation is worse than having no figure at all.

## Status

Early. Built for one reader on four devices. The protocol implementation is the stable
part; everything around it is opinionated and probably wrong for you.

## Setup

```bash
npx wrangler d1 create spine
npx wrangler d1 execute spine --remote --file=./schema.sql
npx wrangler secret put AUTH_PEPPER
npx wrangler deploy
```

The `--remote` flag matters: without it, wrangler executes the schema against a local
development copy and the deployed Worker sees an empty database.

Registration is open only while the `users` table is empty, and closes itself after
the first account. Register **from KOReader**, not with curl: the client sends an MD5
of your password, so an account created by hand must be created with that MD5, not the
password itself — registering from the device sidesteps the mismatch entirely. Set
`ALLOW_REGISTRATION=true` only if you ever need to re-open it.

In KOReader, on **every device, before you download any books**:

1. **Progress sync → Custom sync server**, enter your domain, register/log in
2. **Document matching method → Binary**
3. Enable **Send document metadata** — it's off by default, and it's how the server
   learns titles and authors

## Important: don't touch published files

Binary matching identifies a book by a partial MD5 of its contents. If you re-tag,
recompress, or convert a file after it's on a device, that device forks into a separate
book with separate progress. Normalize your library *before* the files reach any
reader, then leave them alone.

## Auth is weak, and that's the protocol's fault

kosync authenticates with an MD5 of your password in a header, on every request. Every
implementation inherits this, including this one.

Server-side, `spine` stores `HMAC-SHA-256(AUTH_PEPPER, key)` rather than the key, and
compares in constant time. It deliberately does not use bcrypt or argon2: a memory-hard
hash on every sync request blows the Workers free-tier CPU budget, and it would be
spent defending a credential the protocol has already capped at unsalted MD5 in a
header. The security model is the one the protocol forces:

**Use a credential you use nowhere else.** The stored hash only needs to survive a
database dump, and a random single-purpose credential survives one fine.

Rate limiting on the auth route is a Cloudflare WAF rule, not Worker code.

## Back it up

The history is the point of the whole exercise, so export it:

```bash
npx wrangler d1 export spine --remote --output=spine-$(date +%F).sql
```

Run that from a systemd timer (`Persistent=true`) on any machine that's sometimes on.
Weekly at minimum; always before a schema change.

## Non-goals

- No DRM handling of any kind
- No multi-user, no sharing, no roles
- Not a library server — pair it with Kavita or Komga

## Related

- [KOReader](https://github.com/koreader/koreader)
- [koreader-sync-server](https://github.com/koreader/koreader-sync-server) — the reference implementation
- [Kavita](https://www.kavitareader.com/) / [Komga](https://komga.org/) — library servers with their own kosync endpoints

## Licence

MIT.
