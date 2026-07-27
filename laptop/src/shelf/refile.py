"""Curated series metadata, applied after publication.

Most exports carry no series at all — in a real Play Books library, 139 of 153
books. Those file correctly as standalone, but a 40-book series then appears as 40
separate one-book series in Kavita, which is technically right and practically
useless.

This is the one sanctioned way to change a published book, and it is safe for a
specific reason: **`doc_hash` is derived from file contents, so moving or renaming
a file cannot change its identity.** Progress, sessions and history all key on that
hash and are untouched. The invariant this system protects is that published
*bytes* never change; where those bytes sit on disk is presentation.

Every move is verified against the recorded hash rather than assumed, and anything
ambiguous is reported and skipped instead of guessed at.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from spinecore.config import ShelfConfig
from spinecore.d1 import UPSERT_DOCUMENT, get_client
from spinecore.partial_md5 import partial_md5

from .ledger import Ledger
from .metadata import BookMeta, MetadataError, embed_epub_metadata
from .naming import target_path


@dataclass
class Plan:
    doc_hash: str
    title: str
    authors: str | None
    series: str
    series_index: float
    source: Path
    dest: Path

    @property
    def moves(self) -> bool:
        return self.source != self.dest


@dataclass
class Problem:
    key: str
    reason: str


def _normalise(title: str) -> str:
    """Loose form for matching: case, punctuation and subtitles all vary.

    Real titles in one export include "Small Gods: Discworld Novel, A" and
    "Raising Steam: (Discworld Novel 40)" for books catalogued simply as
    "Small Gods" and "Raising Steam".
    """
    text = title.lower().split(":")[0]
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def build_plan(
    mapping: list[dict], config: ShelfConfig, ledger: Ledger
) -> tuple[list[Plan], list[Problem]]:
    """Resolve a mapping against the ledger. Reports rather than guesses."""
    rows = ledger.all_published()
    plans: list[Plan] = []
    problems: list[Problem] = []

    for group in mapping:
        author = group.get("author")
        series = group["series"]
        # `set_author` rewrites the author; `author` only selects. Comics from a
        # scene release carry neither ComicInfo nor an author in the filename, so
        # they land under "Unknown Author" and the series is the only handle.
        set_author = group.get("set_author")

        if not group.get("titles"):
            # Whole-series form: act on every book already filed under this series.
            for row in [r for r in rows if (r.series or "") == series]:
                authors = set_author or row.authors
                meta = BookMeta(
                    title=row.title, authors=authors,
                    series=series, series_index=row.series_index,
                )
                source = Path(row.published_path)
                plans.append(
                    Plan(
                        doc_hash=row.doc_hash,
                        title=row.title or series,
                        authors=authors,
                        series=series,
                        series_index=row.series_index or 0.0,
                        source=source,
                        dest=target_path(config.library, meta, source.suffix),
                    )
                )
            if not any(p.series == series for p in plans):
                problems.append(Problem(series, "no books are filed under this series"))
            continue

        for title, index in group["titles"].items():
            wanted = _normalise(title)
            candidates = [
                row
                for row in rows
                if (author is None or (row.authors or "") == author)
                and _normalise(row.title or "") == wanted
            ]
            if not candidates:
                problems.append(Problem(title, "no book in the ledger matches"))
                continue
            if len(candidates) > 1:
                problems.append(Problem(title, f"{len(candidates)} books match; refusing to guess"))
                continue

            row = candidates[0]
            authors = set_author or row.authors
            meta = BookMeta(
                title=title, authors=authors, series=series, series_index=float(index)
            )
            source = Path(row.published_path)
            plans.append(
                Plan(
                    doc_hash=row.doc_hash,
                    title=title,
                    authors=authors,
                    series=series,
                    series_index=float(index),
                    source=source,
                    dest=target_path(config.library, meta, source.suffix),
                )
            )

    return plans, problems


def apply_plan(
    plans: list[Plan], ledger: Ledger, embed: bool = False
) -> tuple[int, list[Problem]]:
    """Move files, then update the ledger and the D1 catalogue.

    With `embed`, EPUBs also get the series written into the file. That changes
    the bytes and therefore the doc_hash — the one thing this system otherwise
    never does — so it is opt-in, and only safe while the book has no progress or
    sessions recorded against it. `shelf refile --embed` checks that first.
    """
    client, _ = get_client()
    moved = 0
    problems: list[Problem] = []

    for plan in plans:
        if not plan.source.is_file():
            problems.append(Problem(plan.title, f"published file missing: {plan.source}"))
            continue

        if plan.moves:
            plan.dest.parent.mkdir(parents=True, exist_ok=True)
            if plan.dest.exists():
                problems.append(Problem(plan.title, f"destination already exists: {plan.dest}"))
                continue
            plan.source.rename(plan.dest)
            # A rename cannot change contents, so this must hold. Checking it is
            # cheap and turns a silent corruption into a loud failure.
            if partial_md5(plan.dest) != plan.doc_hash:
                problems.append(Problem(plan.title, "hash changed during move — aborting"))
                continue
            _prune(plan.source.parent)
            moved += 1

        old_hash = plan.doc_hash
        if embed and plan.dest.suffix.lower() == ".epub":
            try:
                embed_epub_metadata(
                    plan.dest,
                    BookMeta(series=plan.series, series_index=plan.series_index),
                )
            except MetadataError as err:
                problems.append(Problem(plan.title, f"could not embed series: {err}"))
                continue

            plan.doc_hash = partial_md5(plan.dest)
            if plan.doc_hash != old_hash and client is not None:
                # The old row now names a book that no longer exists anywhere.
                client.query("DELETE FROM documents WHERE doc_hash = ?", [old_hash])

        ledger.conn.execute(
            "UPDATE ingested SET doc_hash = ? WHERE doc_hash = ?", (plan.doc_hash, old_hash)
        )
        ledger.conn.execute(
            """UPDATE ingested
               SET published_path = ?, title = ?, authors = ?, series = ?, series_index = ?
               WHERE doc_hash = ?""",
            (str(plan.dest), plan.title, plan.authors, plan.series, plan.series_index,
             plan.doc_hash),
        )
        if client is not None:
            client.query(
                UPSERT_DOCUMENT,
                # authors must be carried through: the catalogue upsert overwrites,
                # so passing None here would blank it.
                [plan.doc_hash, plan.title, plan.authors, plan.series, plan.series_index,
                 plan.dest.name, 0],
            )

    ledger.conn.commit()
    return moved, problems


def _prune(directory: Path) -> None:
    """Remove a series folder left empty by a move."""
    try:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    except OSError:
        pass


def load_mapping(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]
