# Deploying spine

Order matters in two places: the canary check happens **before** any device is
pointed at the server, and the backup timer goes in on day one, not at Phase 4.

All commands run from `worker/` unless noted.

---

## 1. Authenticate

```fish
npx wrangler login
```

Interactive browser flow. `npx wrangler whoami` should then show your account.

## 2. Create the database

```fish
npx wrangler d1 create spine
```

Copy the `database_id` it prints into `worker/wrangler.jsonc`, replacing
`REPLACE_WITH_DATABASE_ID`.

> `account_id` is deliberately absent from the config — with a single account
> wrangler infers it, and leaving it out keeps one less identifier in a public
> repo. Set `CLOUDFLARE_ACCOUNT_ID` if you ever have more than one.

## 3. Apply the schema

```fish
npx wrangler d1 migrations apply spine --remote
```

**`--remote` matters.** Without it wrangler writes to a local development copy and
the deployed Worker sees an empty database — with no error to tell you so.

Verify:

```fish
npx wrangler d1 execute spine --remote --command "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
```

Expect `book_stats`, `book_status`, `daily_stats`, `documents`, `progress`,
`rollup_meta`, `sessions`, `users`.

## 4. Set the auth pepper

```fish
openssl rand -hex 32 | npx wrangler secret put AUTH_PEPPER
```

This value is unrecoverable once set. If you lose it, every stored `key_hash`
becomes meaningless and every device has to re-register.

## 5. Deploy

```fish
npx wrangler deploy
```

## 6. Verify route precedence — before touching any device

The static site stays on Pages. Workers routes execute in front of whatever else
serves the hostname, so there is no migration and no domain switchover. Prove that
is actually true here rather than discovering it mid-sync:

```fish
# The Worker must answer this
curl -s https://kirtanjain.com/spine-canary/ping
# → {"served_by":"spine-worker"}

# And the Pages site must still answer this
curl -sI https://kirtanjain.com/ | head -1
# → HTTP/2 200, serving the existing site

curl -s https://kirtanjain.com/api/healthcheck
# → {"state":"OK"}
```

If the canary returns your site's 404 page instead, the route is not taking
precedence — stop and fix that before continuing. Once verified, the
`/spine-canary/*` route and its handler can be deleted; they cost nothing to keep.

## 7. Rate limit the auth routes

Do this in the dashboard, **not** in Worker code. Implementing rate limiting as D1
counters converts an attack into a write-quota bill.

**Security → WAF → Rate limiting rules → Create rule** (the free plan includes
one):

- **Expression:** `(http.request.uri.path contains "/users/")`
- **Characteristics:** IP
- **Rate:** 10 requests per 10 seconds
- **Action:** Block, 60 seconds

## 8. Register the account — from KOReader, not curl

The client sends an MD5 of your password, so an account created by hand would have
to be created with that MD5 rather than the password. Registering from the device
sidesteps the mismatch entirely.

Registration is open only while `users` is empty and closes itself after the first
account. See `docs/DEVICE-SETUP.md` for the on-device steps.

**Use a random credential you use nowhere else.** This is the real security
control; everything the server does is secondary to it.

## 9. Install the backup timer — day one, not later

The history is the product. D1 Time Travel is a convenience, not a backup.

```fish
cd ../systemd && ./install.fish
```

See `systemd/README.md`. Run it manually once to confirm it works:

```fish
systemctl --user start spine-backup.service
journalctl --user -u spine-backup.service -n 20
ls -la ~/spine-data/backups/
```

## 10. D1 API token — needed only for Phase 2

The Worker exposes no admin surface; all write access beyond kosync lives in a
laptop-side credential. The ingest pipeline's catalogue sync needs its own token.

**dash.cloudflare.com → My Profile → API Tokens → Create Token → Create Custom
Token:**

- **Permissions:** `Account` → `D1` → `Edit`
- **Account Resources:** your account only
- **TTL:** optional, but set one

Then, on the laptop:

```fish
cp laptop/.env.example laptop/.env
# fill in CF_ACCOUNT_ID, CF_D1_DATABASE_ID, CF_API_TOKEN
```

`laptop/.env` is gitignored. Do not put this token anywhere near the Worker.

---

## Routine operations

```fish
npx wrangler tail                       # live logs; check auth CPU stays in budget
npx wrangler d1 export spine --remote --output=spine-(date +%F).sql
npx wrangler deploy                     # after any src change
npx wrangler d1 migrations apply spine --remote   # after any new migration
```

**Always export before applying a migration.**

## Changing the conflict policy

`CONFLICT_POLICY` is a plain var, so switching it is a config edit and a redeploy —
no data migration, because `progress` keeps one row per device and resolution is
computed on read.

```jsonc
"vars": { "CONFLICT_POLICY": "furthest" }
```

The PRD's advice is to live on `newest` for two weeks of real use before deciding.
