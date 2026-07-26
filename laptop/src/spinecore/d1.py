"""Minimal D1 HTTP client.

Used by both the ingest pipeline's catalogue sync and the statistics importer.
The Worker exposes no admin surface by design, so every write beyond the kosync
protocol comes through here with a scoped API token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import CloudflareConfig


class D1Error(RuntimeError):
    pass


@dataclass
class D1Client:
    config: CloudflareConfig
    timeout: float = 30.0

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        """Run one statement and return its rows."""
        return self._post({"sql": sql, "params": params or []})

    def batch(self, statements: list[tuple[str, list[Any]]]) -> None:
        """Run several statements.

        D1's HTTP API takes one `sql` string, so multiple statements are sent as
        separate requests rather than pretending to be a transaction. Every write
        this client issues is an idempotent upsert or an INSERT with a
        deterministic id, so a partial batch is safe to simply re-run.
        """
        for sql, params in statements:
            self.query(sql, params)

    def _post(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        response = httpx.post(
            self.config.query_url,
            json=payload,
            headers={"Authorization": f"Bearer {self.config.api_token}"},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise D1Error(f"D1 HTTP {response.status_code}: {response.text[:500]}")

        body = response.json()
        if not body.get("success", False):
            raise D1Error(f"D1 error: {body.get('errors')}")

        rows: list[dict[str, Any]] = []
        for result in body.get("result", []):
            rows.extend(result.get("results", []) or [])
        return rows


UPSERT_DOCUMENT = """
INSERT INTO documents (doc_hash, title, authors, series, series_index, filename, first_seen)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(doc_hash) DO UPDATE SET
  title        = excluded.title,
  authors      = excluded.authors,
  series       = excluded.series,
  series_index = excluded.series_index,
  filename     = excluded.filename
"""
"""Straight overwrite, unlike the Worker's metadata-push upsert which only fills
gaps. The pipeline is the curated source: its comic titles come from ComicInfo
rather than from whatever the file happens to be called, so it wins."""


INSERT_SESSION = """
INSERT INTO sessions (id, doc_hash, title, authors, device_id, started_at, ended_at,
                      duration_s, pages, source, confidence, method, imported_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO NOTHING
"""
"""Session ids are derived from the natural key, so re-running an importer is a
no-op by construction rather than by a de-duplication pass."""
