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
_AUTHOR_WITH_SORT = re.compile(r"^(?P<name>.*?)\s*\[(?P<sort>[^\]]*)\]\s*$")


def _normalise_author(part: str) -> str:
    """One author, with calibre's sort form removed and sort order undone.

    Two different problems share this bracket. Normally the display name and the
    sort form differ — ``Will Wight [Wight, Will]`` — and the bracket is simply
    noise that has to go, or the naming step's comma split files the whole series
    under ``Will Wight [Wight``.

    But when the two are *identical* and contain a comma, as in
    ``Jordan, Robert [Jordan, Robert]``, the source metadata is itself in sort
    order. That equality is the signal: flipping on a comma alone would turn
    "Terry Pratchett, Neil Gaiman" into one mangled name, whereas here calibre is
    telling us the display name was never in display order to begin with.
    """
    match = _AUTHOR_WITH_SORT.match(part.strip())
    if not match:
        return part.strip()

    name, sort = match.group("name").strip(), match.group("sort").strip()
    if sort and name == sort and name.count(",") == 1:
        last, _, first = name.partition(",")
        if last.strip() and first.strip():
            return f"{first.strip()} {last.strip()}"
    return name


def clean_authors(value: str | None) -> str | None:
    """Normalise calibre's author string, or None if it says nothing."""
    if not value:
        return None
    names = []
    for part in re.split(r"\s*&\s*", value):
        name = _normalise_author(part)
        if name and name.lower() != "unknown":
            names.append(name)
    return " & ".join(names) or None


def embed_epub_metadata(path: Path, meta: BookMeta) -> None:
    """Write series metadata into an EPUB, in place.

    Kavita's Book-type libraries take the series from inside the file and ignore
    the folder path entirely — the opposite of how it treats comics, where
    ComicInfo.xml and the path both work. A book with no embedded series is a
    standalone series of one to Kavita, however neatly it is filed on disk.

    **This changes the file, and therefore its doc_hash.** It must only ever run
    before the file reaches a device: afterwards it forks the book and orphans
    whatever progress and sessions were recorded against the old hash.
    """
    if meta.series is None:
        return

    command = ["ebook-meta", str(path), "--series", meta.series]
    if meta.series_index is not None:
        index = meta.series_index
        command += ["--index", str(int(index) if index == int(index) else index)]

    proc = run_tool(command, timeout=300)
    if proc.returncode != 0:
        raise MetadataError(f"ebook-meta could not write series: {proc.stderr.strip()[:200]}")


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


#: Scanner, quality and group tags trailing the issue number. A real release is
#: named "Alias 002 (2001) (Digital) (Zone-Empire)", not "Alias 002".
_TAG_GROUP = re.compile(r"[(\[{][^)\]}]*[)\]}]")

#: ``Alias 002``, ``Saga #12``, ``Berserk v03``
_ISSUE = re.compile(
    r"^(?P<series>.+?)[ _-]+(?:(?P<marker>#|v|vol\.?|volume)[ _]?)?(?P<index>\d{1,4})$",
    re.I,
)


def parse_comic_filename(filename: str) -> BookMeta:
    """Last-resort parse, for comics with no embedded metadata at all.

    Deliberately narrow. A wrong series assignment silently merges two series in
    Kavita and leaves no trace, so anything short of a confident match goes to
    quarantine instead.

    The confidence test is an explicit marker (``#``, ``v``, ``vol``) or a
    zero-padded number. Without one, "Fahrenheit 451" parses just as happily as
    "Alias 002" and files a novel as issue 451 of a series called Fahrenheit.
    """
    stem = _TAG_GROUP.sub(" ", Path(filename).stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -_")

    match = _ISSUE.match(stem)
    if not match:
        return BookMeta(source="none")

    series = match.group("series").strip(" -_")
    raw = match.group("index")
    if len(series) < 2 or (not match.group("marker") and not raw.startswith("0")):
        return BookMeta(source="none")

    index = float(raw)
    plain = int(index) if index == int(index) else index
    return BookMeta(
        title=f"{series} #{plain}",
        series=series,
        series_index=index,
        source="filename",
    )


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
