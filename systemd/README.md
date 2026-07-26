# systemd units

User units — no root required.

```fish
./install.fish
sudo loginctl enable-linger $USER   # so timers run while logged out
```

## What gets installed

| Unit | Does |
|---|---|
| `spine-backup.timer` → `.service` | Weekly `wrangler d1 export --remote`, gzipped into `~/spine-data/backups`, keeping the last 12 |
| `shelf-ingest.path` → `.service` | Runs `shelf ingest` when `~/inbox` changes |

## Why `Persistent=true`

The laptop is asleep most of the time. A timer that fires while the machine is off
is skipped outright unless it is persistent — the backup would then quietly never
run, which is the worst possible failure for the one thing that cannot be
regenerated.

## Why the backup goes in on day one

D1 Time Travel is a convenience, not a backup. Every measured session is
unrepeatable: the reading already happened, and no amount of later effort
reconstructs it. Everything else in this repo can be rebuilt from source.

`backup.sh` deliberately fails on an empty export rather than rotating a good
backup out for a useless one.

## Checking on them

```fish
systemctl --user list-timers spine-backup.timer
systemctl --user start spine-backup.service     # run it now
journalctl --user -u spine-backup.service -n 40
journalctl --user -u shelf-ingest.service -n 40

ls -la ~/spine-data/backups/
```

## Restoring

```fish
gunzip -c ~/spine-data/backups/spine-2026-07-27.sql.gz > /tmp/restore.sql
npx wrangler d1 create spine-restore
npx wrangler d1 execute spine-restore --remote --file=/tmp/restore.sql
```

Point `wrangler.jsonc` at the restored database id and deploy. Verifying that a
restore actually works is a Phase 1 acceptance criterion — do it once, early,
rather than finding out during an incident.
