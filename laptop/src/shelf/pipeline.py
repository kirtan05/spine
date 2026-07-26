"""The ingest pipeline.

    watch -> identify -> convert -> spreads -> tag -> name -> publish -> record -> sync

Two invariants hold the whole thing together:

**Published files are immutable.** Every transformation happens before the file
reaches the library, because a file that changes after a device has downloaded it
becomes a different book with separate progress.

**Idempotency is keyed on the source content hash.** Re-running over the same
input reads the ledger and stops, without converting, hashing, or writing.
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from spinecore.config import ShelfConfig
from spinecore.d1 import UPSERT_DOCUMENT, get_client
from spinecore.partial_md5 import content_sha256, partial_md5

from . import convert, spreads
from . import publish as publishing
from .identify import Identity, Kind, identify, published_suffix
from .ledger import Entry, Ledger
from .metadata import BookMeta, MetadataError, extract
from .naming import target_path


class Action(StrEnum):
    PUBLISHED = "published"
    SKIPPED = "skipped"
    QUARANTINED = "quarantined"


@dataclass
class Outcome:
    action: Action
    source: Path
    reason: str = ""
    dest: Path | None = None
    doc_hash: str | None = None

    def describe(self) -> str:
        arrow = f" -> {self.dest}" if self.dest else ""
        detail = f" ({self.reason})" if self.reason else ""
        return f"{self.action.value:<12} {self.source.name}{arrow}{detail}"


def ingest_file(source: Path, config: ShelfConfig, ledger: Ledger) -> Outcome:
    source_hash = content_sha256(source)

    existing = ledger.published(source_hash)
    if existing is not None:
        return Outcome(
            Action.SKIPPED, source, "already ingested",
            Path(existing.published_path), existing.doc_hash,
        )
    if ledger.quarantined(source_hash) is not None:
        return Outcome(Action.SKIPPED, source, "already quarantined")

    identity = identify(source)
    if identity.kind is Kind.UNKNOWN:
        reason = f"unrecognised-format-{identity.detail}"
        return _quarantine(source, source_hash, reason, config, ledger)

    try:
        meta = extract(source, identity)
    except MetadataError as err:
        return _quarantine(source, source_hash, f"metadata-error-{err}", config, ledger)

    if not meta.is_usable:
        # Never guessed at. A wrong series assignment silently merges two series
        # in Kavita and leaves no signal that it happened.
        return _quarantine(source, source_hash, "no-usable-metadata", config, ledger)

    with tempfile.TemporaryDirectory(prefix="shelf-work-") as tmp:
        work = Path(tmp)
        try:
            payload, transformed = _prepare(source, identity, meta, config, work)
        except (convert.ConversionError, spreads.SpreadsUnavailable) as err:
            return _quarantine(source, source_hash, f"conversion-failed-{err}", config, ledger)

        try:
            dest = target_path(config.library, meta, published_suffix(identity, source))
        except ValueError as err:
            return _quarantine(source, source_hash, f"unnameable-{err}", config, ledger)

        if dest.exists():
            if content_sha256(dest) == content_sha256(payload):
                # Same bytes already in place — a ledger that was rebuilt or lost,
                # not a collision. Record it so the next run short-circuits.
                doc_hash = partial_md5(dest)
                _record(ledger, source, source_hash, doc_hash, dest, identity, meta)
                source.unlink(missing_ok=True)
                return Outcome(Action.SKIPPED, source, "already in library", dest, doc_hash)
            return _quarantine(source, source_hash, "name-collision", config, ledger)

        publishing.publish(payload, dest)
        doc_hash = partial_md5(dest)

        if transformed:
            # The published file is not the original. Keep the original: the KCC
            # pass is lossy, and a RAR that failed to repack cleanly is only
            # diagnosable against the thing it came from.
            publishing.archive_original(source, config.archive, source_hash)

        _record(ledger, source, source_hash, doc_hash, dest, identity, meta)
        source.unlink(missing_ok=True)

    return Outcome(Action.PUBLISHED, source, meta.source, dest, doc_hash)


def _prepare(
    source: Path, identity: Identity, meta: BookMeta, config: ShelfConfig, work: Path
) -> tuple[Path, bool]:
    """Produce the exact bytes that will be published. Returns (payload, transformed)."""
    if not identity.is_comic:
        # EPUBs, PDFs and MOBIs pass through untouched. Rewriting an EPUB to
        # embed tags would change its identity for no gain — Kavita reads the OPF
        # and the catalogue in D1 carries everything else.
        return source, False

    cbz = work / f"{source.stem}.cbz"
    convert.convert_to_cbz(source, identity.kind, cbz, meta)
    if config.spreads_enabled:
        cbz = spreads.process(cbz, config.spreads_profile, work / "kcc")
    return cbz, True


def _record(
    ledger: Ledger,
    source: Path,
    source_hash: str,
    doc_hash: str,
    dest: Path,
    identity: Identity,
    meta: BookMeta,
) -> None:
    ledger.record_published(
        Entry(
            content_sha256=source_hash,
            doc_hash=doc_hash,
            source_name=source.name,
            published_path=str(dest),
            kind=identity.kind.value,
            title=meta.title,
            authors=meta.authors,
            series=meta.series,
            series_index=meta.series_index,
            metadata_source=meta.source,
            synced_to_d1=0,
            ingested_at=int(time.time()),
        )
    )


def _quarantine(
    source: Path, source_hash: str, reason: str, config: ShelfConfig, ledger: Ledger
) -> Outcome:
    dest = publishing.quarantine(source, reason, source_hash, config.quarantine)
    ledger.record_quarantined(source_hash, source.name, reason, dest)
    source.unlink(missing_ok=True)
    return Outcome(Action.QUARANTINED, source, reason, dest)


def ingest_paths(paths: list[Path], config: ShelfConfig, ledger: Ledger) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for path in sorted(paths):
        if path.is_file() and not path.name.startswith("."):
            outcomes.append(ingest_file(path, config, ledger))
    return outcomes


def inbox_files(config: ShelfConfig) -> list[Path]:
    if not config.inbox.is_dir():
        return []
    return [
        p for p in sorted(config.inbox.rglob("*"))
        if p.is_file() and not p.name.startswith(".")
    ]


def sync_catalogue(ledger: Ledger) -> tuple[int, int, str]:
    """Push pending catalogue rows to D1. Returns (synced, pending, note).

    Separate from publication because the laptop is often offline. What must not
    happen is a published file with no `documents` row by the time a device opens
    it — that renders as "Unknown" on the reading page, which is the signal that
    this step was missed.
    """
    pending = ledger.unsynced()
    if not pending:
        return 0, 0, "nothing pending"

    client, how = get_client()
    if client is None:
        return 0, len(pending), how

    done: list[str] = []
    for entry in pending:
        client.query(
            UPSERT_DOCUMENT,
            [
                entry.doc_hash,
                entry.title,
                entry.authors,
                entry.series,
                entry.series_index,
                Path(entry.published_path).name,
                entry.ingested_at,
            ],
        )
        done.append(entry.content_sha256)

    ledger.mark_synced(done)
    return len(done), 0, how
