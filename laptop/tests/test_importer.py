"""Round-trip against a database built to KOReader's actual statistics schema.

The schema here is copied from plugins/statistics.koplugin/main.lua so that a
column rename upstream shows up as a test failure rather than as an importer that
silently finds nothing.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from kostats.importer import (
    READEST_FILENAME,
    STATS_FILENAME,
    find_databases,
    import_device,
    koreader_hashes,
    read_device,
    source_for,
)

KOREADER_SCHEMA = """
CREATE TABLE book (
    id integer PRIMARY KEY autoincrement,
    title text, authors text, notes integer, last_open integer,
    highlights integer, pages integer, series text, language text,
    md5 text, total_read_time integer, total_read_pages integer
);
CREATE TABLE page_stat_data (
    id_book integer,
    page integer NOT NULL DEFAULT 0,
    start_time integer NOT NULL DEFAULT 0,
    duration integer NOT NULL DEFAULT 0,
    total_pages integer NOT NULL DEFAULT 0,
    UNIQUE (id_book, page, start_time),
    FOREIGN KEY(id_book) REFERENCES book(id)
);
"""

BASE = 1_700_000_000
MD5 = "9f2c1b7e4a3d5c6f8091a2b3c4d5e6f7"


def make_stats_db(path: Path, books: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(KOREADER_SCHEMA)
    for book_id, book in enumerate(books, start=1):
        conn.execute(
            "INSERT INTO book (id, title, authors, md5, pages) VALUES (?, ?, ?, ?, ?)",
            (book_id, book["title"], book.get("authors"), book.get("md5"), 300),
        )
        for page, start, duration in book.get("events", []):
            conn.execute(
                "INSERT INTO page_stat_data (id_book, page, start_time, duration, total_pages)"
                " VALUES (?, ?, ?, ?, ?)",
                (book_id, page, start, duration, 300),
            )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def stats_root(tmp_path):
    root = tmp_path / "koreader-stats"
    make_stats_db(
        root / "pixel" / STATS_FILENAME,
        [
            {
                "title": "Gardens of the Moon",
                "authors": "Steven Erikson",
                "md5": MD5,
                "events": [
                    (1, BASE, 120),
                    (2, BASE + 120, 180),
                    (3, BASE + 300, 60),
                    # Next evening — a separate session.
                    (4, BASE + 90_000, 240),
                ],
            }
        ],
    )
    make_stats_db(
        root / "tab-s11" / STATS_FILENAME,
        [{"title": "Unhashed Book", "authors": None, "md5": None, "events": [(1, BASE, 60)]}],
    )
    return root


def test_discovers_one_database_per_device(stats_root):
    found = find_databases(stats_root)
    assert set(found) == {"pixel", "tab-s11"}
    assert found["pixel"].name == STATS_FILENAME


def test_ignores_hidden_directories(stats_root):
    # Syncthing's file versioning keeps old copies under .stversions/ inside the
    # synced folder. Read as a device, it would fork a phantom ".stversions".
    versions = stats_root / "pixel" / ".stversions"
    versions.mkdir()
    shutil.copy2(stats_root / "pixel" / STATS_FILENAME, versions / STATS_FILENAME)

    assert set(find_databases(stats_root)) == {"pixel", "tab-s11"}


def test_discovers_a_readest_database_as_its_own_device(stats_root):
    # Readest writes KOReader's schema, keyed by the same partialMD5, but names
    # the file statistics.db.
    make_stats_db(
        stats_root / "pixel-readest" / READEST_FILENAME,
        [{"title": "A", "md5": MD5, "events": [(1, BASE, 60)]}],
    )
    found = find_databases(stats_root)
    assert set(found) == {"pixel", "tab-s11", "pixel-readest"}
    assert source_for(found["pixel-readest"]) == "readest"
    assert source_for(found["pixel"]) == "koreader"


def test_two_databases_in_one_directory_is_an_error(stats_root):
    # The directory name is the device id; two readers sharing one would merge
    # their histories under a single device.
    make_stats_db(stats_root / "pixel" / READEST_FILENAME, [])
    with pytest.raises(ValueError, match="pixel"):
        find_databases(stats_root)


class RecordingClient:
    def __init__(self):
        self.params: list[list] = []

    def query(self, sql, params=None):
        self.params.append(params)
        return []


def test_sessions_are_labelled_with_the_reader_that_measured_them(tmp_path):
    events = [{"title": "A", "md5": MD5, "events": [(1, BASE, 60)]}]
    ko = make_stats_db(tmp_path / "pixel" / STATS_FILENAME, events)
    rd = make_stats_db(tmp_path / "pixel-readest" / READEST_FILENAME, events)

    ko_client, rd_client = RecordingClient(), RecordingClient()
    assert import_device("pixel", ko, ko_client).source == "koreader"
    assert import_device("pixel-readest", rd, rd_client).source == "readest"

    (ko_row,), (rd_row,) = ko_client.params, rd_client.params
    assert ko_row[9] == "koreader" and rd_row[9] == "readest"
    assert ko_row[0] != rd_row[0]  # session ids never collide across readers


def test_groups_page_events_into_sessions(stats_root):
    books, skipped = read_device(stats_root / "pixel" / STATS_FILENAME)
    assert skipped == 0
    assert len(books) == 1

    book, sessions = books[0]
    assert book.md5 == MD5
    assert book.title == "Gardens of the Moon"
    assert len(sessions) == 2
    assert sessions[0].duration_s == 360
    assert sessions[0].pages == 3
    assert sessions[1].duration_s == 240


def test_books_without_an_md5_are_skipped_not_guessed(stats_root):
    """No md5 means filename matching was used. It cannot be joined to a
    document, and inventing a join would corrupt another book's history."""
    books, skipped = read_device(stats_root / "tab-s11" / STATS_FILENAME)
    assert books == []
    assert skipped == 1


def test_collects_koreaders_own_hashes_for_verification(stats_root):
    hashes = koreader_hashes(stats_root)
    assert hashes["pixel"] == {MD5}
    assert hashes["tab-s11"] == set()


def test_reads_a_wal_mode_database_without_losing_recent_rows(tmp_path):
    """A synced copy opened read-only without its -wal is stale, and looks like a
    successful import that is quietly missing the most recent reading."""
    source = tmp_path / "device" / STATS_FILENAME
    make_stats_db(source, [{"title": "A", "md5": MD5, "events": [(1, BASE, 60)]}])

    conn = sqlite3.connect(source)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "INSERT INTO page_stat_data (id_book, page, start_time, duration, total_pages)"
        " VALUES (1, 2, ?, 90, 300)",
        (BASE + 60,),
    )
    conn.commit()
    conn.close()

    books, _ = read_device(source)
    _, sessions = books[0]
    assert sessions[0].duration_s == 150
    assert sessions[0].pages == 2


def test_missing_root_is_reported_not_crashed(tmp_path):
    assert find_databases(tmp_path / "nope") == {}
