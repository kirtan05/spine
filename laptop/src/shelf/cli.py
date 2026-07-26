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
from .pipeline import Action, inbox_files, ingest_paths, sync_catalogue


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
            paths = args.paths or inbox_files(config)
            if not paths:
                print("inbox is empty")
                return 0

            outcomes = ingest_paths(paths, config, ledger)
            for outcome in outcomes:
                print(outcome.describe())

            tally = {action: 0 for action in Action}
            for outcome in outcomes:
                tally[outcome.action] += 1
            print(
                f"\n{tally[Action.PUBLISHED]} published, "
                f"{tally[Action.SKIPPED]} skipped, "
                f"{tally[Action.QUARANTINED]} quarantined"
            )

            synced, pending, note = sync_catalogue(ledger)
            print(f"catalogue: {synced} synced, {pending} pending ({note})")
            # A quarantined file is a decision waiting to be made, not a failure
            # of this run, so it does not fail the systemd unit.
            return 0

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
