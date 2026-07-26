#!/usr/bin/env bash
# Export the D1 database. The reading history is the product of this whole
# system; D1 Time Travel is a convenience, not a backup.
set -euo pipefail

BACKUP_DIR="${SPINE_BACKUP_DIR:-$HOME/spine-data/backups}"
KEEP="${SPINE_BACKUP_KEEP:-12}"
WORKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../worker" && pwd)"

mkdir -p "$BACKUP_DIR"
cd "$WORKER_DIR"

stamp="$(date +%F)"
out="$BACKUP_DIR/spine-$stamp.sql"

npx --yes wrangler d1 export spine --remote --output "$out"

# An export that produced nothing is worse than no export, because it looks like
# a backup. Fail loudly rather than rotating a good one out for an empty one.
if [ ! -s "$out" ]; then
    echo "backup produced an empty file: $out" >&2
    rm -f "$out"
    exit 1
fi

gzip -f "$out"
echo "wrote $out.gz ($(du -h "$out.gz" | cut -f1))"

# Keep the most recent N, delete the rest.
ls -1t "$BACKUP_DIR"/spine-*.sql.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
    echo "pruning $old"
    rm -f "$old"
done
