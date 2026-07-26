"""`shelf` — the ingest pipeline's command line.

Invoked by a systemd path unit on ~/inbox, and by hand for backfills.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from spinecore.config import shelf_config
from spinecore.partial_md5 import partial_md5

from .ledger import Ledger
from .pipeline import Action, exclusive, inbox_files, ingest_paths, sync_catalogue


def _ingest(paths: list[Path], config, ledger: Ledger) -> int:
    """Drain the inbox, re-scanning until a pass finds nothing new.

    The re-scan matters because files can land while a run is in progress — which
    is exactly when someone is copying a batch in — and a single snapshot of the
    directory would strand them until the next trigger.
    """
    explicit = bool(paths)
    total = {action: 0 for action in Action}

    for _ in range(20):
        batch = paths if explicit else inbox_files(config)
        if not batch:
            break

        for outcome in ingest_paths(batch, config, ledger):
            print(outcome.describe())
            total[outcome.action] += 1
        if explicit:
            break

    if not any(total.values()):
        print("inbox is empty")
        return 0

    print(
        f"\n{total[Action.PUBLISHED]} published, "
        f"{total[Action.SKIPPED]} skipped, "
        f"{total[Action.QUARANTINED]} quarantined"
    )
    synced, pending, note = sync_catalogue(ledger)
    print(f"catalogue: {synced} synced, {pending} pending ({note})")
    # A quarantined file is a decision waiting to be made, not a failure of this
    # run, so it does not fail the systemd unit.
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shelf", description="spine ingest pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="ingest files (default: everything in the inbox)")
    ingest.add_argument("paths", nargs="*", type=Path)

    sub.add_parser("sync-catalogue", help="push pending documents rows to D1")
    sub.add_parser("status", help="ledger counts and configured paths")

    hash_cmd = sub.add_parser("hash", help="print KOReader's doc_hash for a file")
    hash_cmd.add_argument("paths", nargs="+", type=Path)

    args = parser.parse_args(argv)
    config = shelf_config()

    if args.command == "hash":
        for path in args.paths:
            print(f"{partial_md5(path)}  {path}")
        return 0

    with Ledger(config.ledger) as ledger:
        if args.command == "ingest":
            with exclusive(config) as acquired:
                if not acquired:
                    # Not an error: the run that holds the lock is doing the work.
                    print("another ingest is already running; nothing to do")
                    return 0
                return _ingest(args.paths, config, ledger)

        if args.command == "sync-catalogue":
            synced, pending, note = sync_catalogue(ledger)
            print(f"{synced} synced, {pending} pending ({note})")
            return 1 if pending else 0

        if args.command == "status":
            counts = ledger.counts()
            print(f"inbox       {config.inbox}")
            print(f"library     {config.library}")
            print(f"quarantine  {config.quarantine}")
            print(f"archive     {config.archive}")
            print(f"ledger      {config.ledger}")
            spreads = f"on ({config.spreads_profile})" if config.spreads_enabled else "off"
            print(f"spreads     {spreads}")
            print()
            print(f"published   {counts['published']}")
            print(f"unsynced    {counts['unsynced']}")
            print(f"quarantined {counts['quarantined']}")
            if counts["unsynced"]:
                print(
                    "\nWARNING: published files without a D1 catalogue row render as "
                    '"Unknown" on the reading page. Run: shelf sync-catalogue'
                )
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
