"""Local record of everything the pipeline has done.

Idempotency is keyed on the **source** content hash, so re-running over the same
input is a no-op without re-reading, re-converting, or re-hashing anything.

`synced_to_d1` is tracked separately from publication because the laptop is often
offline. A book can be published locally now and appear in the catalogue later;
what must never happen is a published file with no `documents` row by the time a
device opens it, which is what `shelf status` reports on.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingested (
  content_sha256   TEXT PRIMARY KEY,
  doc_hash         TEXT NOT NULL,
  source_name      TEXT NOT NULL,
  published_path   TEXT NOT NULL,
  kind             TEXT NOT NULL,
  title            TEXT,
  authors          TEXT,
  series           TEXT,
  series_index     REAL,
  metadata_source  TEXT,
  synced_to_d1     INTEGER NOT NULL DEFAULT 0,
  ingested_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingested_doc ON ingested(doc_hash);
CREATE INDEX IF NOT EXISTS idx_ingested_sync ON ingested(synced_to_d1);

CREATE TABLE IF NOT EXISTS quarantined (
  content_sha256   TEXT PRIMARY KEY,
  source_name      TEXT NOT NULL,
  reason           TEXT NOT NULL,
  path             TEXT NOT NULL,
  quarantined_at   INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class Entry:
    content_sha256: str
    doc_hash: str
    source_name: str
    published_path: str
    kind: str
    title: str | None
    authors: str | None
    series: str | None
    series_index: float | None
    metadata_source: str | None
    synced_to_d1: int
    ingested_at: int


class Ledger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- reads ------------------------------------------------------------- #

    def published(self, content_sha256: str) -> Entry | None:
        row = self.conn.execute(
            "SELECT * FROM ingested WHERE content_sha256 = ?", (content_sha256,)
        ).fetchone()
        return Entry(**dict(row)) if row else None

    def quarantined(self, content_sha256: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM quarantined WHERE content_sha256 = ?", (content_sha256,)
        ).fetchone()

    def unsynced(self) -> list[Entry]:
        rows = self.conn.execute("SELECT * FROM ingested WHERE synced_to_d1 = 0").fetchall()
        return [Entry(**dict(row)) for row in rows]

    def counts(self) -> dict[str, int]:
        published = self.conn.execute("SELECT COUNT(*) FROM ingested").fetchone()[0]
        unsynced = self.conn.execute(
            "SELECT COUNT(*) FROM ingested WHERE synced_to_d1 = 0"
        ).fetchone()[0]
        quarantined = self.conn.execute("SELECT COUNT(*) FROM quarantined").fetchone()[0]
        return {"published": published, "unsynced": unsynced, "quarantined": quarantined}

    # -- writes ------------------------------------------------------------ #

    def record_published(self, entry: Entry) -> None:
        self.conn.execute(
            """INSERT INTO ingested (content_sha256, doc_hash, source_name, published_path, kind,
                                     title, authors, series, series_index, metadata_source,
                                     synced_to_d1, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(content_sha256) DO NOTHING""",
            (
                entry.content_sha256, entry.doc_hash, entry.source_name, entry.published_path,
                entry.kind, entry.title, entry.authors, entry.series, entry.series_index,
                entry.metadata_source, entry.synced_to_d1, entry.ingested_at or int(time.time()),
            ),
        )
        self.conn.commit()

    def record_quarantined(
        self, content_sha256: str, source_name: str, reason: str, path: Path
    ) -> None:
        self.conn.execute(
            """INSERT INTO quarantined (content_sha256, source_name, reason, path, quarantined_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(content_sha256) DO UPDATE SET reason = excluded.reason,
                                                         path = excluded.path""",
            (content_sha256, source_name, reason, str(path), int(time.time())),
        )
        self.conn.commit()

    def mark_synced(self, content_hashes: list[str]) -> None:
        self.conn.executemany(
            "UPDATE ingested SET synced_to_d1 = 1 WHERE content_sha256 = ?",
            [(h,) for h in content_hashes],
        )
        self.conn.commit()

    def doc_hashes(self) -> dict[str, str]:
        """doc_hash -> published path, for the statistics importer's md5 check."""
        rows = self.conn.execute("SELECT doc_hash, published_path FROM ingested").fetchall()
        return {row["doc_hash"]: row["published_path"] for row in rows}
