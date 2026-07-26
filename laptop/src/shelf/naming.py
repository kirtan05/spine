"""Destination paths, shaped for Kavita's series detection.

    {Author}/{Series}/{Series} {NN} - {Title}.{ext}

Standalone books still get a series-shaped folder so Kavita groups them as a
one-book series rather than scattering them at the library root.
"""

from __future__ import annotations

import re
from pathlib import Path

from .metadata import BookMeta

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING = " ."
MAX_COMPONENT = 120


def sanitise(value: str) -> str:
    """Make one path component safe without making it unrecognisable."""
    cleaned = _ILLEGAL.sub("", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(_TRAILING)
    if len(cleaned) > MAX_COMPONENT:
        cleaned = cleaned[:MAX_COMPONENT].rstrip(_TRAILING)
    return cleaned


def format_index(index: float | None) -> str | None:
    """Zero-pad so lexical order matches reading order."""
    if index is None:
        return None
    if index == int(index):
        return f"{int(index):02d}" if index < 100 else str(int(index))
    return f"{index:g}"


def primary_author(authors: str | None) -> str:
    """KOReader and calibre both join multiple authors; file under the first."""
    if not authors:
        return "Unknown Author"
    first = re.split(r"\s*(?:&|,|\n|;)\s*", authors.strip())[0]
    return sanitise(first) or "Unknown Author"


def _synthetic_title(meta: BookMeta) -> str | None:
    """The title parse_comic_filename invents when a comic has no metadata."""
    if not meta.series or meta.series_index is None:
        return None
    index = meta.series_index
    return f"{meta.series} #{int(index) if index == int(index) else index}"


def target_path(library: Path, meta: BookMeta, extension: str) -> Path:
    """Where a book belongs. Raises if there is not enough metadata to decide."""
    if not meta.is_usable:
        raise ValueError("insufficient metadata for a destination path")

    extension = extension if extension.startswith(".") else f".{extension}"
    author = primary_author(meta.authors)
    series = sanitise(meta.series) if meta.series else None
    title = sanitise(meta.title) if meta.title else None

    if series:
        index = format_index(meta.series_index)
        # A title synthesised from series and number carries nothing the filename
        # does not already say, and repeating it reads as "Alias 02 - Alias #2".
        informative = title and title != _synthetic_title(meta)
        stem = f"{series} {index} - {title}" if index and informative else (
            f"{series} {index}" if index else (title or series)
        )
        return library / author / series / f"{sanitise(stem)}{extension}"

    # Standalone: the title doubles as the series folder.
    assert title is not None  # guaranteed by is_usable when series is absent
    return library / author / title / f"{title}{extension}"
