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
from .pipeline import (
    Action,
    exclusive,
    inbox_files,
    ingest_paths,
    prune_inbox,
    retry_quarantine,
    sync_catalogue,
)
from .reconcile import apply_plans as reconcile_apply
from .reconcile import build_plan as reconcile_plan
from .reconcile import load_catalogue
from .refile import apply_plan, build_plan, load_mapping

#: Curated catalogue: authors.json, books.json, and one file per group of series.
MAPPINGS = Path(__file__).resolve().parents[2] / "mappings"


def _already_read(doc_hashes: set[str]) -> set[str]:
    """Which of these books already have reading history in D1.

    Embedding metadata rewrites the file and changes its identity. Doing that to
    a book that has been read orphans its progress and measured sessions with no
    way back, so it is checked rather than assumed.
    """
    from spinecore.d1 import get_client

    client, _ = get_client()
    if client is None:
        return set()
    rows = client.query("SELECT doc_hash FROM progress")
    rows += client.query("SELECT DISTINCT doc_hash FROM sessions WHERE doc_hash IS NOT NULL")
    return {r["doc_hash"] for r in rows} & doc_hashes


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
    prune_inbox(config)
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

    ref = sub.add_parser("refile", help="apply curated series metadata to published books")
    ref.add_argument("mapping", type=Path, help="JSON: [{author, series, titles: {title: index}}]")
    ref.add_argument("--apply", action="store_true", help="actually move files (default: dry run)")
    ref.add_argument("--embed", action="store_true",
                     help="also write the series into EPUBs (changes doc_hash; refuses if read)")
    rec = sub.add_parser(
        "reconcile",
        help="make every EPUB's embedded metadata, path and catalogue row match mappings/",
    )
    rec.add_argument("--mappings", type=Path, default=MAPPINGS)
    rec.add_argument("--apply", action="store_true",
                     help="rewrite, move and migrate (default: dry run). Changes doc_hash of "
                          "rewritten books; their progress and sessions are moved with them")
    sub.add_parser("retry-quarantine", help="return quarantined files to the inbox and re-try")
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

        if args.command == "retry-quarantine":
            returned = retry_quarantine(config, ledger)
            print(f"{returned} file(s) returned to the inbox")
            if returned:
                with exclusive(config) as acquired:
                    if acquired:
                        return _ingest([], config, ledger)
                print("another ingest is running; it will pick them up")
            return 0

        if args.command == "refile":
            plans, problems = build_plan(load_mapping(args.mapping), config, ledger)
            for plan in plans:
                marker = "move" if plan.moves else "keep"
                print(f"  {marker}  {plan.series} {plan.series_index:g} - {plan.title}")
            for problem in problems:
                print(f"  SKIP  {problem.key}: {problem.reason}")

            if not args.apply:
                print(f"\n{len(plans)} to refile, {len(problems)} skipped (dry run; pass --apply)")
                return 0

            if args.embed:
                read = _already_read({p.doc_hash for p in plans})
                if read:
                    print(f"\nREFUSED: {len(read)} of these books already have progress or")
                    print("sessions recorded. Embedding changes doc_hash, which would orphan")
                    print("that history permanently. Re-run without --embed.")
                    return 1

            moved, failures = apply_plan(plans, ledger, embed=args.embed)
            for failure in failures:
                print(f"  FAILED {failure.key}: {failure.reason}")
            print(f"\n{moved} moved, {len(plans) - moved} already in place, {len(failures)} failed")
            return 1 if failures else 0

        if args.command == "reconcile":
            return _reconcile(args, config, ledger)

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


def _reconcile(args, config, ledger: Ledger) -> int:
    from spinecore.d1 import get_client

    plans, problems = reconcile_plan(ledger, config, load_catalogue(args.mappings))
    changing = [p for p in plans if p.changes]
    for plan in changing:
        what = "rewrite+move" if plan.rewrites and plan.moves else (
            "rewrite" if plan.rewrites else "move")
        rel = plan.dest.relative_to(config.library)
        print(f"  {what:<12} {rel}")
    for problem in problems:
        print(f"  SKIP         {problem.key}: {problem.reason}")

    rewrites = sum(p.rewrites for p in changing)
    summary = (f"{len(changing)} to change ({rewrites} rewritten, so new doc_hash), "
               f"{len(plans) - len(changing)} already right, {len(problems)} skipped")
    if not args.apply:
        print(f"\n{summary} (dry run; pass --apply)")
        return 0

    client, note = get_client()
    if client is None and rewrites:
        # Without D1 the old hashes' progress and sessions could not be moved.
        print(f"\nREFUSED: {note}. Rewriting without D1 would orphan reading history.")
        return 1
    done, failures = reconcile_apply(plans, ledger, client)
    for failure in failures:
        print(f"  FAILED       {failure.key}: {failure.reason}")
    print(f"\n{done} reconciled, {len(failures)} failed ({note})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
