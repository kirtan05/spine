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

import fcntl
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
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
from .metadata import BookMeta, MetadataError, embed_epub_metadata, extract
from .naming import target_path
from .reconcile import Catalogue, Problem, desired_metadata, write_epub_metadata


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


def _apply_catalogue(
    source: Path, meta: BookMeta, catalogue: Catalogue
) -> BookMeta | str:
    """The curated catalogue, as `shelf reconcile` applies it to the library.

    Applied at ingest too, so a second edition of a book already in the library
    resolves to the same destination and is caught as a collision instead of
    being published beside it under a different path.
    """
    stub = Entry(content_sha256="", doc_hash="", source_name=source.name,
                 published_path="", kind="epub", title=None, authors=None, series=None,
                 series_index=None, metadata_source=meta.source, synced_to_d1=0,
                 ingested_at=0)
    desired = desired_metadata(stub, meta, catalogue)
    return desired.reason if isinstance(desired, Problem) else desired


def ingest_file(
    source: Path, config: ShelfConfig, ledger: Ledger, catalogue: Catalogue | None = None
) -> Outcome:
    # Another run may have taken this file between the directory scan and here.
    if not source.is_file():
        return Outcome(Action.SKIPPED, source, "taken by another run")

    source_hash = content_sha256(source)

    # A skipped file still has to leave the inbox. Left there it is not merely
    # untidy: the path unit re-triggers on the directory, the next run skips it
    # again, and the two spin against each other indefinitely.
    existing = ledger.published(source_hash)
    if existing is not None and Path(existing.published_path).is_file():
        source.unlink(missing_ok=True)
        return Outcome(
            Action.SKIPPED, source, "already ingested",
            Path(existing.published_path), existing.doc_hash,
        )

    quarantined = ledger.quarantined(source_hash)
    if quarantined is not None and Path(quarantined["path"]).is_file():
        source.unlink(missing_ok=True)
        return Outcome(Action.SKIPPED, source, "already quarantined")

    # Falling through when the recorded copy has gone is deliberate: the ledger
    # says we handled these bytes, but the file it points at no longer exists, so
    # re-publishing is right and deleting the only remaining copy would not be.

    identity = identify(source)
    if identity.kind is Kind.UNKNOWN:
        reason = f"unrecognised-format-{identity.detail}"
        return _quarantine(source, source_hash, reason, config, ledger)

    with tempfile.TemporaryDirectory(prefix="shelf-work-") as tmp:
        work = Path(tmp)

        # An EPUB zipped together with its folder has no OCF root: calibre invents
        # metadata from the filename and readers cannot open it. Repaired before
        # anything reads it, so metadata and the published bytes both come from
        # the valid layout.
        readable, repaired = source, False
        if identity.kind is Kind.EPUB and convert.nested_epub_root(source):
            readable = convert.flatten_nested_epub(source, work / "repaired" / source.name)
            repaired = True

        try:
            meta = extract(readable, identity)
        except MetadataError as err:
            return _quarantine(source, source_hash, f"metadata-error-{err}", config, ledger)

        if not meta.is_usable:
            # Never guessed at. A wrong series assignment silently merges two series
            # in Kavita and leaves no signal that it happened.
            return _quarantine(source, source_hash, "no-usable-metadata", config, ledger)

        embedded = meta
        if catalogue is not None and identity.kind is Kind.EPUB:
            curated = _apply_catalogue(source, meta, catalogue)
            if isinstance(curated, str):
                return _quarantine(source, source_hash, f"catalogue-{curated}", config, ledger)
            meta = curated

        try:
            payload, transformed = _prepare(readable, identity, meta, config, work, embedded)
        except (convert.ConversionError, spreads.SpreadsUnavailable) as err:
            return _quarantine(source, source_hash, f"conversion-failed-{err}", config, ledger)
        transformed = transformed or repaired

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
    source: Path, identity: Identity, meta: BookMeta, config: ShelfConfig, work: Path,
    embedded: BookMeta | None = None,
) -> tuple[Path, bool]:
    """Produce the exact bytes that will be published. Returns (payload, transformed)."""
    if not identity.is_comic:
        # The curated catalogue disagrees with what the file says: write it in
        # now, before publication, so the book gets its final hash exactly once.
        if identity.kind is Kind.EPUB and embedded is not None and _differs(embedded, meta):
            staged = work / source.name
            shutil.copy2(source, staged)
            write_epub_metadata(staged, meta)
            return staged, True

        # An EPUB whose series came from somewhere other than the file itself has
        # to carry it internally, because Kavita's Book libraries read the OPF and
        # ignore the folder path. Done here, before publication, so the hash is
        # computed once on the final bytes and the immutability rule holds.
        if identity.kind is Kind.EPUB and meta.series and meta.source != "ebook-meta":
            staged = work / source.name
            shutil.copy2(source, staged)
            embed_epub_metadata(staged, meta)
            return staged, True

        # Otherwise pass through untouched: the file already agrees with the
        # catalogue, and rewriting it would change its identity for nothing.
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


@contextmanager
def exclusive(config: ShelfConfig) -> Iterator[bool]:
    """Hold the pipeline lock, or yield False if another run already has it.

    The systemd path unit fires on every change to the inbox, so a manual
    `shelf ingest` and a triggered one can overlap trivially — copying a batch of
    files in while a run is in progress is enough. Two processes over one inbox
    means one deletes a file the other is part-way through hashing.
    """
    config.data_dir.mkdir(parents=True, exist_ok=True)
    with open(config.data_dir / "shelf.lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        # The lock releases when the file closes, i.e. when this block exits —
        # including if the process dies, which is why it is flock and not a
        # sentinel file that would need cleaning up after a crash.
        yield True


def ingest_paths(
    paths: list[Path], config: ShelfConfig, ledger: Ledger, catalogue: Catalogue | None = None
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for path in sorted(paths):
        if path.is_file() and not path.name.startswith("."):
            outcomes.append(ingest_file(path, config, ledger, catalogue))
    return outcomes


def _differs(a: BookMeta, b: BookMeta) -> bool:
    def key(m: BookMeta) -> tuple:
        return (m.title, m.authors, m.series,
                None if m.series_index is None else float(m.series_index))
    return key(a) != key(b)


def prune_inbox(config: ShelfConfig) -> None:
    """Remove directories the inbox is left with once their files are taken.

    Comics arrive as a folder per series. The files inside get published, the
    empty folder stays, and `shelf status` reports an inbox that looks unprocessed.
    """
    if not config.inbox.is_dir():
        return
    dirs = (p for p in config.inbox.rglob("*") if p.is_dir() and not _hidden(p, config.inbox))
    for path in sorted(dirs, reverse=True):
        try:
            if not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass


def retry_quarantine(config: ShelfConfig, ledger: Ledger) -> int:
    """Return quarantined files to the inbox so they can be tried again.

    Quarantine is a decision deferred, not a verdict. When metadata handling
    improves — a filename pattern the parser did not know, a tool that was not
    installed — the files already set aside deserve another pass, and the ledger
    rows that record the old refusal have to go with them.
    """
    rows = ledger.conn.execute(
        "SELECT content_sha256, path, source_name FROM quarantined"
    ).fetchall()
    returned = 0

    for row in rows:
        path = Path(row["path"])
        if path.is_file():
            dest = config.inbox / row["source_name"]
            if dest.exists():
                dest = config.inbox / f"{row['content_sha256'][:8]}-{row['source_name']}"
            config.inbox.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), dest)
            returned += 1
        ledger.conn.execute(
            "DELETE FROM quarantined WHERE content_sha256 = ?", (row["content_sha256"],)
        )

    ledger.conn.commit()
    return returned


def _hidden(path: Path, root: Path) -> bool:
    """Any dot-component below the inbox: Syncthing's .stfolder/, .stignore and
    .syncthing.*.tmp transfers when the phone feeds ~/inbox/phone."""
    return any(part.startswith(".") for part in path.relative_to(root).parts)


def inbox_files(config: ShelfConfig) -> list[Path]:
    if not config.inbox.is_dir():
        return []
    return [
        p for p in sorted(config.inbox.rglob("*"))
        if p.is_file() and not _hidden(p, config.inbox)
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
