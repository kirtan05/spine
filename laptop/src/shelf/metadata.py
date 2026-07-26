"""Metadata extraction.

Nothing here guesses. A file whose series or title cannot be established from its
own contents goes to quarantine, because a wrong series assignment is worse than
an unfiled book: it silently merges two series in Kavita and there is no signal
that it happened.

The one exception is a conservative filename parse for comics, gated on patterns
specific enough that a match is not really a guess.
"""

from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path

from spinecore.process import run as run_tool

from .identify import Identity, Kind


@dataclass(frozen=True)
class BookMeta:
    title: str | None = None
    authors: str | None = None
    series: str | None = None
    series_index: float | None = None
    #: Where this came from, for the ledger and for debugging a bad filing.
    source: str = "none"

    @property
    def is_usable(self) -> bool:
        """Enough to file the book without guessing."""
        return bool(self.title) or bool(self.series)


class MetadataError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# EPUB
# --------------------------------------------------------------------------- #

_EBOOK_META_FIELDS = {
    "Title": "title",
    "Author(s)": "authors",
    "Series": "series",
}


def epub_metadata(path: Path) -> BookMeta:
    """Read EPUB metadata with calibre's ebook-meta.

    Read-only: the EPUB is never rewritten. Rewriting would change the partial
    MD5 for no benefit — Kavita reads the OPF directly, and the catalogue in D1
    carries everything else.
    """
    try:
        proc = run_tool(["ebook-meta", str(path)], timeout=120)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise MetadataError(f"ebook-meta failed: {err}") from err

    if proc.returncode != 0:
        raise MetadataError(f"ebook-meta exited {proc.returncode}: {proc.stderr.strip()[:200]}")

    fields: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        if key in _EBOOK_META_FIELDS:
            fields[_EBOOK_META_FIELDS[key]] = value.strip()

    series, index = _split_series(fields.get("series"))

    return BookMeta(
        title=fields.get("title") or None,
        authors=clean_authors(fields.get("authors")),
        series=series,
        series_index=index,
        source="ebook-meta",
    )


#: calibre prints the author-sort form after the name: ``Will Wight [Wight, Will]``.
_AUTHOR_SORT = re.compile(r"\s*\[[^\]]*\]\s*$")


def clean_authors(value: str | None) -> str | None:
    """Strip calibre's bracketed author-sort and its literal "Unknown".

    Left in, the sort form becomes part of the directory name — and because the
    naming step splits multiple authors on commas, ``Will Wight [Wight, Will]``
    files the whole Cradle series under a folder called ``Will Wight [Wight``.
    """
    if not value:
        return None
    names = []
    for part in re.split(r"\s*&\s*", value):
        name = _AUTHOR_SORT.sub("", part).strip()
        if name and name.lower() != "unknown":
            names.append(name)
    return " & ".join(names) or None


def _split_series(value: str | None) -> tuple[str | None, float | None]:
    """calibre renders series as ``Name #3`` or ``Name #3.5``."""
    if not value:
        return None, None
    match = re.match(r"^(?P<name>.*?)\s*#\s*(?P<index>\d+(?:\.\d+)?)\s*$", value.strip())
    if match:
        return match.group("name").strip() or None, float(match.group("index"))
    return value.strip() or None, None


# --------------------------------------------------------------------------- #
# Comics
# --------------------------------------------------------------------------- #


def comic_metadata(path: Path) -> BookMeta:
    """ComicInfo.xml first, comictagger second, filename last."""
    meta = _comic_info_from_archive(path)
    if meta.is_usable:
        return meta

    meta = _comictagger(path)
    if meta.is_usable:
        return meta

    return parse_comic_filename(path.name)


def _comic_info_from_archive(path: Path) -> BookMeta:
    try:
        with zipfile.ZipFile(path) as archive:
            name = next(
                (n for n in archive.namelist() if Path(n).name.lower() == "comicinfo.xml"),
                None,
            )
            if name is None:
                return BookMeta()
            raw = archive.read(name)
    except (zipfile.BadZipFile, OSError, KeyError):
        return BookMeta()

    return parse_comic_info(raw)


def parse_comic_info(raw: bytes) -> BookMeta:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return BookMeta()

    def text(tag: str) -> str | None:
        node = root.find(tag)
        value = (node.text or "").strip() if node is not None else ""
        return value or None

    series = text("Series")
    number = text("Number")
    index: float | None = None
    if number:
        try:
            index = float(number)
        except ValueError:
            index = None

    writers = text("Writer")
    title = text("Title")
    # A comic issue's own Title is often empty; the series plus number is the
    # real identity, so synthesise a display title rather than dropping it.
    if not title and series and number:
        title = f"{series} #{number}"

    return BookMeta(
        title=title,
        authors=writers,
        series=series,
        series_index=index,
        source="comicinfo",
    )


def _comictagger(path: Path) -> BookMeta:
    """Fall back to comictagger for archives with metadata we do not parse."""
    try:
        proc = run_tool(["comictagger", "--print", "--type", "cr", str(path)], timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return BookMeta()
    if proc.returncode != 0 or not proc.stdout.strip():
        return BookMeta()

    fields: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip()

    series = fields.get("series") or None
    number = fields.get("issue") or fields.get("number") or None
    index: float | None = None
    if number:
        try:
            index = float(number)
        except ValueError:
            index = None
    return replace(
        BookMeta(
            title=fields.get("title") or (f"{series} #{number}" if series and number else None),
            authors=fields.get("writer") or None,
            series=series,
            series_index=index,
        ),
        source="comictagger",
    )


# ``Saga 012 (2013).cbz``, ``Saga v03 (2014).cbz``, ``Saga #12.cbz``
_FILENAME_PATTERNS = (
    re.compile(
        r"^(?P<series>.+?)[ _-]+(?:#|v|vol\.?|volume)?[ _]?(?P<index>\d{1,4})(?:\s*\(\d{4}\))?$",
        re.I,
    ),
)


def parse_comic_filename(filename: str) -> BookMeta:
    """Last-resort parse, deliberately narrow.

    Only matches ``<series> <number>`` with an optional volume marker and year.
    Anything looser starts merging unrelated series, which is exactly the failure
    quarantine exists to prevent.
    """
    stem = Path(filename).stem.strip()
    stem = re.sub(r"\s+", " ", stem)
    for pattern in _FILENAME_PATTERNS:
        match = pattern.match(stem)
        if not match:
            continue
        series = match.group("series").strip(" -_")
        if not series or len(series) < 2:
            continue
        index = float(match.group("index"))
        return BookMeta(
            title=f"{series} #{match.group('index')}",
            series=series,
            series_index=index,
            source="filename",
        )
    return BookMeta(source="none")


def extract(path: Path, identity: Identity) -> BookMeta:
    if identity.kind is Kind.EPUB:
        return epub_metadata(path)
    if identity.is_comic:
        return comic_metadata(path)
    if identity.kind in {Kind.PDF, Kind.MOBI}:
        # calibre reads both; it just cannot always find much.
        try:
            return epub_metadata(path)
        except MetadataError:
            return BookMeta(source="none")
    return BookMeta(source="none")
