"""Identify what a file actually is.

Magic bytes first, extension second. Plenty of files named ``.cbr`` are ZIPs and
plenty named ``.cbz`` are RARs — routing on the extension means handing a RAR to a
ZIP reader and calling the resulting failure "corrupt".
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".jxl"}


class Kind(StrEnum):
    EPUB = "epub"
    COMIC_ZIP = "comic_zip"       # CBZ: a ZIP whose payload is images
    COMIC_RAR = "comic_rar"       # CBR and friends, needs conversion
    COMIC_SEVENZIP = "comic_7z"
    PDF = "pdf"
    MOBI = "mobi"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Identity:
    kind: Kind
    #: Why we decided, for the quarantine reason when the answer is UNKNOWN.
    detail: str = ""

    @property
    def needs_conversion(self) -> bool:
        return self.kind in {Kind.COMIC_RAR, Kind.COMIC_SEVENZIP}

    @property
    def is_comic(self) -> bool:
        return self.kind in {Kind.COMIC_ZIP, Kind.COMIC_RAR, Kind.COMIC_SEVENZIP}


_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_RAR4 = b"Rar!\x1a\x07\x00"
_RAR5 = b"Rar!\x1a\x07\x01\x00"
_SEVENZIP = b"7z\xbc\xaf\x27\x1c"
_PDF = b"%PDF-"


def identify(path: str | Path) -> Identity:
    path = Path(path)
    with open(path, "rb") as handle:
        head = handle.read(8)
        handle.seek(60)
        at60 = handle.read(8)

    if head.startswith(_RAR5) or head.startswith(_RAR4):
        return Identity(Kind.COMIC_RAR, "rar archive")
    if head.startswith(_SEVENZIP):
        return Identity(Kind.COMIC_SEVENZIP, "7z archive")
    if head.startswith(_PDF):
        return Identity(Kind.PDF, "pdf")
    # MOBI/AZW3 put their type/creator at offset 60.
    if at60.startswith(b"BOOKMOBI") or at60.startswith(b"TPZ"):
        return Identity(Kind.MOBI, "mobi-family")
    if any(head.startswith(magic) for magic in _ZIP_MAGICS):
        return _identify_zip(path)

    return Identity(Kind.UNKNOWN, f"unrecognised magic bytes {head[:8]!r}")


def _identify_zip(path: Path) -> Identity:
    """A ZIP is an EPUB, a comic archive, or something we refuse to guess at."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            # The EPUB spec requires an uncompressed `mimetype` entry first.
            if "mimetype" in names:
                mimetype = archive.read("mimetype").strip()
                if mimetype == b"application/epub+zip":
                    return Identity(Kind.EPUB, "zip with epub mimetype")
            if any(name.lower().endswith(".opf") for name in names):
                return Identity(Kind.EPUB, "zip containing an OPF package")

            files = [n for n in names if not n.endswith("/")]
            images = [n for n in files if Path(n).suffix.lower() in IMAGE_SUFFIXES]
            # Ignore ComicInfo.xml and macOS resource forks when judging.
            meaningful = [
                n for n in files
                if not n.startswith("__MACOSX/")
                and Path(n).name not in {"ComicInfo.xml", ".DS_Store"}
            ]
            if images and len(images) >= max(1, int(len(meaningful) * 0.8)):
                return Identity(Kind.COMIC_ZIP, f"{len(images)} images")

            return Identity(Kind.UNKNOWN, f"zip with {len(images)}/{len(meaningful)} images")
    except zipfile.BadZipFile as err:
        return Identity(Kind.UNKNOWN, f"corrupt zip: {err}")


def published_suffix(identity: Identity, source: Path) -> str:
    """The extension a file should be published under, from what it *is*.

    Not from what it was called. In a Google Play Books export, 71 of 156 files
    are named ``.pdf`` and every one of them is an EPUB — publishing those under
    the source extension hands a zip to a PDF renderer in both KOReader and
    Kavita. The MOBI family keeps its own extension because .mobi, .azw and .azw3
    are genuinely different containers.
    """
    if identity.is_comic:
        return ".cbz"
    if identity.kind is Kind.EPUB:
        return ".epub"
    if identity.kind is Kind.PDF:
        return ".pdf"
    return source.suffix.lower() or ".bin"


def is_probably_stable(path: Path, previous_size: int | None) -> bool:
    """Size-stability fallback for multi-part downloads.

    The systemd path unit reacts to close-for-write, which is the right signal for
    a single-file download. A multi-part download closes the file repeatedly, so
    this is checked as well before anything touches it.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return size > 0 and previous_size == size
