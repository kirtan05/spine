"""D1 access for the laptop-side tools.

Two transports, because the Worker deliberately exposes no admin surface and every
write beyond the kosync protocol has to come from here:

- :class:`HttpD1Client` — the D1 REST API with a scoped API token. Preferred: it
  is a narrow credential that can be revoked on its own, and it does not depend on
  a Node toolchain being present.
- :class:`WranglerD1Client` — shells out to ``wrangler d1 execute``, reusing the
  OAuth session from ``wrangler login``. No token to create, but it borrows a
  credential far broader than D1:Edit, so it is the fallback rather than the
  default.

The PRD names both. :func:`get_client` picks whichever is available.
"""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import CloudflareConfig, cloudflare_config
from .process import run as run_tool

WORKER_DIR = Path(__file__).resolve().parents[3] / "worker"


class D1Error(RuntimeError):
    pass


class D1Client(Protocol):
    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]: ...


# --------------------------------------------------------------------------- #
# REST API
# --------------------------------------------------------------------------- #


@dataclass
class HttpD1Client:
    config: CloudflareConfig
    timeout: float = 30.0

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        response = httpx.post(
            self.config.query_url,
            json={"sql": sql, "params": [_json_param(p) for p in (params or [])]},
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


def _json_param(value: Any) -> Any:
    """D1's HTTP API binds only JSON scalars; booleans have to become integers."""
    if isinstance(value, bool):
        return 1 if value else 0
    return value


# --------------------------------------------------------------------------- #
# wrangler
# --------------------------------------------------------------------------- #


@dataclass
class WranglerD1Client:
    """Runs statements through ``wrangler d1 execute --remote``.

    ``wrangler d1 execute`` takes SQL text, not bound parameters, so values are
    inlined as SQL literals by :func:`sql_literal`. That function is the security
    boundary for this transport and is tested directly — a title containing an
    apostrophe is not a hypothetical, it is most of the library.
    """

    database: str = "spine"
    cwd: Path = WORKER_DIR
    timeout: float = 120.0

    #: Well under ARG_MAX. Every statement this client issues is a single row's
    #: worth of metadata, so hitting this means something is wrong upstream.
    MAX_STATEMENT = 500_000

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        statement = inline_params(sql, params or [])
        if len(statement) > self.MAX_STATEMENT:
            raise D1Error(f"statement of {len(statement)} chars is too large for --command")

        # `--command`, not `--file`: the file form prints a run summary instead of
        # the rows, so a SELECT through it silently returns query statistics.
        # subprocess passes this as one argv element, so there is no shell to
        # escape through — the escaping that matters is inline_params'.
        proc = run_tool(
            ["npx", "--yes", "wrangler", "d1", "execute", self.database,
             "--remote", "--json", "--command", statement],
            timeout=self.timeout,
            cwd=self.cwd,
        )

        if proc.returncode != 0:
            raise D1Error(f"wrangler exited {proc.returncode}: {proc.stderr.strip()[:500]}")
        return _rows_from_wrangler(proc.stdout)


def _rows_from_wrangler(stdout: str) -> list[dict[str, Any]]:
    """Pull the JSON payload out of wrangler's output.

    npx and wrangler both prepend notices even under --json, so the payload starts
    at the first bracket rather than at the first byte.
    """
    start = min((i for i in (stdout.find("["), stdout.find("{")) if i != -1), default=-1)
    if start == -1:
        return []
    try:
        payload = json.loads(stdout[start:])
    except json.JSONDecodeError as err:
        raise D1Error(f"could not parse wrangler output: {err}") from err

    if isinstance(payload, dict):
        payload = [payload]
    rows: list[dict[str, Any]] = []
    for result in payload:
        if isinstance(result, dict):
            rows.extend(result.get("results", []) or [])
    return rows


def sql_literal(value: Any) -> str:
    """Render a Python value as a SQLite literal.

    Strings double their single quotes, which is SQLite's only escape inside a
    quoted literal. NUL bytes are stripped because SQLite cannot hold them in a
    text literal at all.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise D1Error(f"cannot store non-finite number: {value}")
        return repr(value)
    if isinstance(value, (bytes, bytearray)):
        return "X'" + bytes(value).hex() + "'"
    text = str(value).replace("\x00", "")
    return "'" + text.replace("'", "''") + "'"


def inline_params(sql: str, params: list[Any]) -> str:
    """Substitute ``?`` placeholders with literals, ignoring ``?`` inside strings."""
    out: list[str] = []
    index = 0
    in_string = False
    i = 0
    while i < len(sql):
        char = sql[i]
        if in_string:
            out.append(char)
            if char == "'":
                # A doubled quote is an escaped quote, not the end of the literal.
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if char == "'":
            in_string = True
            out.append(char)
        elif char == "?":
            if index >= len(params):
                raise D1Error("more ? placeholders than parameters")
            out.append(sql_literal(params[index]))
            index += 1
        else:
            out.append(char)
        i += 1

    if index != len(params):
        raise D1Error(f"{len(params)} parameters for {index} placeholders")
    return "".join(out)


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #


def wrangler_available() -> bool:
    return shutil.which("npx") is not None and WORKER_DIR.is_dir()


def get_client() -> tuple[D1Client | None, str]:
    """Return (client, description). Client is None when neither transport works."""
    config = cloudflare_config()
    if config is not None:
        return HttpD1Client(config), "D1 REST API (scoped token)"
    if wrangler_available():
        return WranglerD1Client(), "wrangler d1 execute (OAuth session)"
    return None, "no D1 access — set CF_API_TOKEN in laptop/.env, or run `wrangler login`"


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
