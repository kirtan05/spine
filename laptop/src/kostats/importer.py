"""Import measured reading time from each device's statistics.sqlite3.

This is the only source of `confidence = 'exact'` rows in the whole system. The
kosync protocol carries position and nothing else — measured time exists solely in
each device's local statistics database — so it is *collected*, not synced.

`book.md5` in that database is the same partial MD5 the sync protocol uses, which
is what lets a session join to a document without any name matching.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from spinecore.d1 import INSERT_SESSION, D1Client, get_client

from .sessions import DEFAULT_GAP_SECONDS, PageEvent, Session, group_sessions, session_id

SOURCE = "koreader"
STATS_FILENAME = "statistics.sqlite3"


@dataclass
class Book:
    title: str | None
    authors: str | None
    md5: str


@dataclass
class DeviceImport:
    device_id: str
    books: int
    sessions: int
    seconds: int
    skipped_no_md5: int


def find_databases(root: Path) -> dict[str, Path]:
    """device_id -> statistics.sqlite3, discovered from directory names.

    The directory name *is* the device id. Renaming one later forks that device's
    session history, because ids are derived from it.
    """
    found: dict[str, Path] = {}
    if not root.is_dir():
        return found
    for candidate in sorted(root.rglob(STATS_FILENAME)):
        device_id = candidate.parent.name
        if device_id and device_id != root.name:
            found[device_id] = candidate
    return found


def _snapshot(path: Path, workdir: Path) -> Path:
    """Copy the database (and any WAL sidecars) before opening it.

    KOReader runs the statistics database in WAL mode where it can. Opening a
    synced copy read-only without its -wal file silently returns data as of the
    last checkpoint — which looks like a successful import that is quietly missing
    the most recent reading. Copying the whole set and opening it normally lets
    SQLite replay the WAL.
    """
    target = workdir / path.name
    shutil.copy2(path, target)
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, target.with_name(target.name + suffix))
    return target


def read_device(path: Path, gap_seconds: int = DEFAULT_GAP_SECONDS) -> tuple[
    list[tuple[Book, list[Session]]], int
]:
    """Return [(book, sessions)] plus a count of books with no usable md5."""
    with tempfile.TemporaryDirectory(prefix="kostats-") as tmp:
        snapshot = _snapshot(path, Path(tmp))
        conn = sqlite3.connect(snapshot)
        conn.row_factory = sqlite3.Row
        try:
            books = conn.execute("SELECT id, title, authors, md5 FROM book").fetchall()
            results: list[tuple[Book, list[Session]]] = []
            skipped = 0

            for row in books:
                md5 = (row["md5"] or "").strip().lower()
                if not md5:
                    # No md5 means the book was read with filename matching, or
                    # predates binary matching. It cannot be joined to a document
                    # and is not worth guessing at.
                    skipped += 1
                    continue

                events = [
                    PageEvent(page=e["page"], start_time=e["start_time"], duration=e["duration"])
                    for e in conn.execute(
                        "SELECT page, start_time, duration FROM page_stat_data WHERE id_book = ?",
                        (row["id"],),
                    )
                ]
                if not events:
                    continue

                book = Book(title=row["title"], authors=row["authors"], md5=md5)
                results.append((book, group_sessions(events, gap_seconds)))
            return results, skipped
        finally:
            conn.close()


def import_device(
    device_id: str,
    path: Path,
    client: D1Client | None,
    gap_seconds: int = DEFAULT_GAP_SECONDS,
) -> DeviceImport:
    books, skipped = read_device(path, gap_seconds)
    now = int(time.time())
    total_sessions = 0
    total_seconds = 0

    for book, sessions in books:
        for session in sessions:
            total_sessions += 1
            total_seconds += session.duration_s
            if client is None:
                continue
            client.query(
                INSERT_SESSION,
                [
                    session_id(SOURCE, device_id, book.md5, session.started_at),
                    book.md5,
                    book.title,
                    book.authors,
                    device_id,
                    session.started_at,
                    session.ended_at,
                    session.duration_s,
                    session.pages,
                    SOURCE,
                    # Measured on-device, page by page. The only rows in the
                    # system that get to claim this.
                    "exact",
                    None,
                    now,
                ],
            )

    return DeviceImport(
        device_id=device_id,
        books=len(books),
        sessions=total_sessions,
        seconds=total_seconds,
        skipped_no_md5=skipped,
    )


def import_all(
    root: Path, gap_seconds: int = DEFAULT_GAP_SECONDS, dry_run: bool = False
) -> tuple[list[DeviceImport], str]:
    databases = find_databases(root)
    if not databases:
        return [], f"no {STATS_FILENAME} found under {root}"

    client: D1Client | None = None
    note = "dry run — nothing written"
    if not dry_run:
        client, note = get_client()
        if client is None:
            return [], note

    return [
        import_device(device_id, path, client, gap_seconds)
        for device_id, path in databases.items()
    ], note


def koreader_hashes(root: Path) -> dict[str, set[str]]:
    """device_id -> the md5s KOReader itself computed.

    These are the ground truth for verifying the ported partialMD5: they were
    produced by KOReader on a real device, over the real files.
    """
    result: dict[str, set[str]] = {}
    for device_id, path in find_databases(root).items():
        with tempfile.TemporaryDirectory(prefix="kostats-") as tmp:
            conn = sqlite3.connect(_snapshot(path, Path(tmp)))
            try:
                rows = conn.execute("SELECT md5 FROM book WHERE md5 IS NOT NULL AND md5 != ''")
                result[device_id] = {str(r[0]).strip().lower() for r in rows}
            finally:
                conn.close()
    return result
