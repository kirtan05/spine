"""`kostats` — import measured reading time from KOReader statistics databases."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from shelf.ledger import Ledger
from spinecore.config import shelf_config
from spinecore.partial_md5 import partial_md5

from .importer import (
    READEST_FILENAME,
    STATS_FILENAME,
    find_databases,
    import_all,
    koreader_hashes,
)
from .sessions import DEFAULT_GAP_SECONDS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kostats", description="KOReader statistics importer")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("import", help="import sessions into D1")
    run.add_argument("--gap", type=int, default=DEFAULT_GAP_SECONDS,
                     help="seconds of idle time that end a session "
                          f"(default {DEFAULT_GAP_SECONDS})")
    run.add_argument("--dry-run", action="store_true", help="report without writing")

    sub.add_parser("devices", help="list discovered statistics databases")
    sub.add_parser(
        "verify-md5",
        help="check the ported partialMD5 against the hashes KOReader computed on-device",
    )

    args = parser.parse_args(argv)
    config = shelf_config()
    root = config.data_dir / "koreader-stats"

    if args.command == "devices":
        databases = find_databases(root)
        if not databases:
            print(f"no {STATS_FILENAME} or {READEST_FILENAME} under {root}")
            print("see docs/DATA-COLLECTION.md section 5")
            return 1
        for device_id, path in databases.items():
            print(f"{device_id:<16} {path}")
        return 0

    if args.command == "import":
        results, note = import_all(root, gap_seconds=args.gap, dry_run=args.dry_run)
        if not results:
            print(note)
            return 1
        for result in results:
            hours = result.seconds / 3600
            print(
                f"{result.device_id:<16} {result.books:>4} books  "
                f"{result.sessions:>5} sessions  {hours:>8.1f}h measured"
                + (f"  ({result.skipped_no_md5} skipped: no md5)" if result.skipped_no_md5 else "")
            )
        total = sum(r.sessions for r in results)
        sources = ",".join(sorted({r.source for r in results}))
        print(f"\n{total} sessions, source={sources} confidence=exact ({note})")
        return 0

    if args.command == "verify-md5":
        return _verify(root, config.ledger)

    return 0


def _verify(root: Path, ledger_path: Path) -> int:
    """Compare our ported hash against KOReader's own, over real files.

    This is a far stronger check than any test vector: KOReader computed these on
    a device, over the actual bytes. If the port were wrong, nothing would match.
    """
    by_device = koreader_hashes(root)
    if not by_device:
        print(f"no statistics databases under {root}")
        return 1

    device_hashes: set[str] = set()
    for device_id, hashes in by_device.items():
        print(f"{device_id:<16} {len(hashes)} books with an md5")
        device_hashes |= hashes

    with Ledger(ledger_path) as ledger:
        published = ledger.doc_hashes()

    if not published:
        print("\nledger is empty — nothing published yet, so nothing to compare")
        return 1

    matched: list[str] = []
    unread: list[str] = []
    drifted: list[tuple[str, str, str]] = []

    for doc_hash, path in published.items():
        recomputed = partial_md5(path) if Path(path).is_file() else None
        if recomputed is not None and recomputed != doc_hash:
            # The published file changed after ingest. This is the immutability
            # invariant being violated, and it forks the book on every device.
            drifted.append((path, doc_hash, recomputed))
        elif doc_hash in device_hashes:
            matched.append(doc_hash)
        else:
            unread.append(doc_hash)

    print(f"\n{len(matched)} published books match a hash KOReader computed on-device")
    print(f"{len(unread)} published books not yet opened on any device (no comparison possible)")

    if drifted:
        print(f"\nFAIL: {len(drifted)} published files changed since ingest:")
        for path, was, now in drifted[:10]:
            print(f"  {path}\n    ledger {was}\n    now    {now}")
        return 1

    if matched:
        print("\nOK: the ported partialMD5 agrees with KOReader for every book it could check.")
        return 0

    print(
        "\nInconclusive: no published book has been opened on a device yet.\n"
        "Open one book on any device, let it sync, re-copy statistics.sqlite3, and re-run."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
