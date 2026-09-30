"""Bring every published EPUB into line with the curated catalogue, in one pass.

`refile` applies one series mapping at a time and only moves files. This goes
further, because a reader that groups by *embedded* metadata — Readest groups by
author and sorts by series index from the OPF — ignores folders entirely. So the
catalogue has to be true inside the file, not just in its path:

1. read what the EPUB itself says (the ground truth `shelf` once misread: see
   ``epub_metadata`` on Takeout's ``.pdf`` naming);
2. correct it from ``mappings/``: per-book fixes, author aliases, series orders;
3. if the file disagrees, write the corrected metadata into it;
4. move it to where that metadata says it belongs;
5. carry the ledger, the D1 catalogue and any reading history along.

Step 3 changes the bytes and therefore the doc_hash. That is safe here for one
reason, and only because it is done deliberately: the OPF is the only part that
changes, so KOReader xpointers and Readest CFIs into the text stay valid, and
every row keyed on the old hash is *moved* to the new one rather than orphaned.

Idempotent: a second run over a reconciled library plans nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from spinecore.config import ShelfConfig
from spinecore.d1 import UPSERT_DOCUMENT, D1Client
from spinecore.partial_md5 import partial_md5
from spinecore.process import run as run_tool

from .ledger import Entry, Ledger
from .metadata import BookMeta, MetadataError, epub_metadata
from .naming import primary_author, target_path

#: Every table whose rows are keyed on a book's doc_hash.
HASH_KEYED_TABLES = ("progress", "sessions", "book_status", "book_stats")

_ARTICLE = re.compile(r"^(the|a|an) ")
#: A retail suffix, not part of the title: "The Maid: A Novel", "Tomorrow... a novel".
#: Only the bare phrase — "A Crossfire Novel" names a series and is left alone.
_MARKETING = re.compile(r"\s*[:\-–—]?\s*\ban?\s+novel\s*$", re.IGNORECASE)


@dataclass
class Catalogue:
    """The curated inputs, all from ``mappings/``."""

    #: Exact author string -> canonical author.
    authors: dict[str, str] = field(default_factory=dict)
    #: Ledger ``source_name`` -> field overrides, for books whose metadata is wrong
    #: in ways no rule should guess at (title and author swapped, no author).
    books: dict[str, dict] = field(default_factory=dict)
    #: ``[{author, series, titles: {canonical title: index}}]``, as for refile.
    series: list[dict] = field(default_factory=list)


@dataclass
class Plan:
    entry: Entry
    current: BookMeta
    desired: BookMeta
    dest: Path

    @property
    def rewrites(self) -> bool:
        return _fields(self.current) != _fields(self.desired)

    @property
    def moves(self) -> bool:
        return Path(self.entry.published_path) != self.dest

    @property
    def changes(self) -> bool:
        return self.rewrites or self.moves


@dataclass
class Problem:
    key: str
    reason: str


def _fields(meta: BookMeta) -> tuple:
    index = None if meta.series_index is None else float(meta.series_index)
    return (meta.title, meta.authors, meta.series, index)


def load_catalogue(directory: Path) -> Catalogue:
    """``authors.json`` and ``books.json`` are special; every other file is series."""
    catalogue = Catalogue()
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "authors.json":
            catalogue.authors.update(data["aliases"])
        elif path.name == "books.json":
            catalogue.books.update(data)
        else:
            catalogue.series.extend(data if isinstance(data, list) else [data])
    return catalogue


def canonical_authors(authors: str | None, aliases: dict[str, str]) -> str | None:
    """Apply aliases to the whole string first, then to each author in it."""
    if not authors:
        return None
    if authors in aliases:
        return aliases[authors]
    names: list[str] = []
    for part in authors.split(" & "):
        name = aliases.get(part, part)
        if name not in names:
            names.append(name)
    return " & ".join(names)


def _words(title: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return _ARTICLE.sub("", text)


def title_matches(actual: str, wanted: str) -> bool:
    """Whole-word containment, ignoring case, punctuation and a leading article.

    Real titles carry their series in every shape — "Cradle 2: Soulsmith",
    "03. Blackflame", "[Liz Carlyle 04] • Liz Carlyle - 04 - Dead Line" — so an
    equality test finds almost nothing. Containment is only safe because matching
    is scoped to one author and a second candidate is refused, not guessed at.
    """
    wanted_words = _words(wanted)
    return bool(wanted_words) and f" {wanted_words} " in f" {_words(actual)} "


def _series_for(
    meta: BookMeta, groups: list[dict]
) -> tuple[str, float, str] | Problem | None:
    author = primary_author(meta.authors)
    hits: list[tuple[str, float, str]] = []
    for group in groups:
        if group.get("author") not in (None, author):
            continue
        for title, index in group.get("titles", {}).items():
            if title_matches(meta.title or "", title):
                hits.append((group["series"], float(index), title))
    if len(hits) > 1:
        # "The Fifth Season" also contains "Season"; the longest wanted title is
        # the specific one. Anything still tied is genuinely ambiguous.
        hits.sort(key=lambda hit: len(_words(hit[2])), reverse=True)
        if len(_words(hits[0][2])) == len(_words(hits[1][2])):
            return Problem(meta.title or "?", f"matches {len(hits)} series entries")
    return hits[0] if hits else None


def desired_metadata(
    entry: Entry, current: BookMeta, catalogue: Catalogue
) -> BookMeta | Problem:
    meta = current
    fix = catalogue.books.get(entry.source_name, {})
    if fix:
        meta = BookMeta(
            title=fix.get("title", meta.title),
            authors=fix.get("authors", meta.authors),
            series=fix.get("series", meta.series),
            series_index=fix.get("series_index", meta.series_index),
            source=meta.source,
        )

    title = meta.title
    if title and "title" not in fix:
        stripped = _MARKETING.sub("", title).strip()
        title = stripped or title
    meta = BookMeta(
        title=title,
        authors=canonical_authors(meta.authors, catalogue.authors),
        series=meta.series,
        series_index=meta.series_index,
        source=meta.source,
    )

    found = _series_for(meta, catalogue.series)
    if isinstance(found, Problem):
        return found
    if found is not None:
        series, index, title = found
        meta = BookMeta(title=title, authors=meta.authors, series=series,
                        series_index=index, source=meta.source)
    return meta


def build_plan(
    ledger: Ledger, config: ShelfConfig, catalogue: Catalogue
) -> tuple[list[Plan], list[Problem]]:
    plans: list[Plan] = []
    problems: list[Problem] = []
    for entry in sorted(ledger.all_published(), key=lambda e: e.published_path):
        path = Path(entry.published_path)
        if path.suffix.lower() != ".epub":
            continue  # comics carry ComicInfo; this is about OPF metadata
        if not path.is_file():
            problems.append(Problem(entry.source_name, f"published file missing: {path}"))
            continue
        try:
            current = epub_metadata(path)
        except MetadataError as err:
            problems.append(Problem(entry.source_name, str(err)))
            continue

        desired = desired_metadata(entry, current, catalogue)
        if isinstance(desired, Problem):
            problems.append(Problem(entry.source_name, desired.reason))
            continue
        if not desired.is_usable:
            problems.append(Problem(entry.source_name, "no usable title"))
            continue
        plans.append(Plan(entry, current, desired, target_path(config.library, desired, ".epub")))

    clashes: dict[Path, list[Plan]] = {}
    for plan in plans:
        clashes.setdefault(plan.dest, []).append(plan)
    for dest, group in clashes.items():
        if len(group) > 1:
            names = ", ".join(p.entry.source_name for p in group)
            problems.append(Problem(str(dest), f"{len(group)} books want this path: {names}"))
            plans = [p for p in plans if p.dest != dest]
    return plans, problems


def write_epub_metadata(path: Path, meta: BookMeta) -> None:
    """Write title, authors and series into the EPUB. Changes its doc_hash."""
    command = ["ebook-meta", str(path)]
    if meta.title:
        command += ["--title", meta.title]
    if meta.authors:
        command += ["--authors", meta.authors]
    # Always passed: an empty value is how ebook-meta *clears* a series, and a
    # wrong one (an omnibus tagged as book one) must be removable, not just left.
    command += ["--series", meta.series or ""]
    if meta.series and meta.series_index is not None:
        index = meta.series_index
        command += ["--index", str(int(index) if index == int(index) else index)]
    proc = run_tool(command, timeout=300)
    if proc.returncode != 0:
        raise MetadataError(f"ebook-meta could not write: {proc.stderr.strip()[:200]}")


def migrate_hash(client: D1Client, old: str, new: str) -> None:
    """Move every row keyed on a book's old hash to its new one."""
    for table in HASH_KEYED_TABLES:
        client.query(f"UPDATE {table} SET doc_hash = ? WHERE doc_hash = ?", [new, old])
    client.query("DELETE FROM documents WHERE doc_hash = ?", [old])


def apply_plans(
    plans: list[Plan], ledger: Ledger, client: D1Client | None
) -> tuple[int, list[Problem]]:
    done = 0
    problems: list[Problem] = []
    for plan in plans:
        if not plan.changes:
            continue
        source = Path(plan.entry.published_path)
        old_hash = plan.entry.doc_hash

        if plan.moves:
            if plan.dest.exists():
                problems.append(Problem(plan.entry.source_name, f"already exists: {plan.dest}"))
                continue
            plan.dest.parent.mkdir(parents=True, exist_ok=True)
            source.rename(plan.dest)
            if partial_md5(plan.dest) != old_hash:
                problems.append(Problem(plan.entry.source_name, "hash changed during move"))
                continue
            _prune(source.parent, stop=plan.dest.parents[2] if len(plan.dest.parents) > 2 else None)

        new_hash = old_hash
        if plan.rewrites:
            try:
                write_epub_metadata(plan.dest, plan.desired)
            except MetadataError as err:
                problems.append(Problem(plan.entry.source_name, str(err)))
                continue
            new_hash = partial_md5(plan.dest)
            # Read it back. calibre reports success for writes it silently skips —
            # it cannot clear an EPUB 3 belongs-to-collection — and an unverified
            # write would be re-planned on every run with nothing said.
            written = epub_metadata(plan.dest)
            if _fields(written) != _fields(plan.desired):
                problems.append(Problem(
                    plan.entry.source_name,
                    f"write did not take: file says {_fields(written)}, "
                    f"wanted {_fields(plan.desired)}",
                ))

        d = plan.desired
        ledger.conn.execute(
            """UPDATE ingested SET doc_hash = ?, published_path = ?, title = ?, authors = ?,
                   series = ?, series_index = ?, synced_to_d1 = 0
               WHERE content_sha256 = ?""",
            (new_hash, str(plan.dest), d.title, d.authors, d.series, d.series_index,
             plan.entry.content_sha256),
        )
        ledger.conn.commit()

        if client is not None:
            if new_hash != old_hash:
                migrate_hash(client, old_hash, new_hash)
            client.query(
                UPSERT_DOCUMENT,
                [new_hash, d.title, d.authors, d.series, d.series_index, plan.dest.name, 0],
            )
            ledger.mark_synced([plan.entry.content_sha256])
        done += 1
    return done, problems


def _prune(directory: Path, stop: Path | None = None) -> None:
    """Remove folders a move left empty, up to (not including) the library root."""
    while directory != stop and directory.is_dir() and not any(directory.iterdir()):
        parent = directory.parent
        try:
            directory.rmdir()
        except OSError:
            return
        directory = parent
