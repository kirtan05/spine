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
| `shelf-ingest.path` → `.service` | Runs `shelf ingest` when `~/inbox` or `~/inbox/phone` changes (inotify is not recursive, so the phone's subfolder is watched explicitly) |
| `kostats-import.timer` → `.service` | `collect-stats.sh` at 00:30: imports every reader's statistics database into D1 |

## Why the stats import runs at 00:30

The Worker's cron rebuilds the page's totals from `sessions` at 01:00 IST. The
import used to run at 02:00, after it, so every night's reading waited an extra day
to appear. `RandomizedDelaySec` is capped at 15 minutes to keep it ahead.

`collect-stats.sh` imports whatever Syncthing has delivered — the laptop does not
need to see the device. Its adb pull is only a fallback for a KOReader device
without Syncthing, and it skips:

- directories Syncthing owns (a `.stfolder` marker): a second writer into a
  receive-only folder shows up as a local change and blocks the next real update;
- devices archived under `~/spine-data/koreader-stats-retired/`: uninstalling
  KOReader leaves `/sdcard/koreader` behind, and the fallback would import it again.

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
journalctl --user -u kostats-import.service -n 40

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
