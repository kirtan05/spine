"""Archive conversion and deterministic CBZ packing.

Two properties matter more than anything else here:

**No image recompression.** Everything is stored, never deflated. Recompressing
would be lossy for no gain and would take an afternoon over a library.

**Byte-for-byte determinism.** The same input must produce the same output on
every run, because the output's partial MD5 is the book's identity. `zip -0`
embeds real mtimes and directory order, so two runs over the same input produce
two different files and therefore two different books. Packing with Python's
zipfile at a pinned timestamp and a sorted entry order removes that entirely.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from spinecore.process import run as run_tool

from .identify import IMAGE_SUFFIXES, Kind
from .metadata import BookMeta

#: The earliest timestamp the ZIP format can represent. Any fixed value works;
#: what matters is that it does not vary between runs.
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
_UNIX_CREATOR = 3
_MODE_644 = 0o100644 << 16


class ConversionError(RuntimeError):
    pass


@contextmanager
def extracted(source: Path, kind: Kind) -> Iterator[Path]:
    """Extract an archive into a temporary directory."""
    with tempfile.TemporaryDirectory(prefix="shelf-extract-") as tmp:
        target = Path(tmp)
        if kind is Kind.COMIC_RAR:
            _run(
                # libarchive/bsdtar's RAR5 support is unreliable; unar is not.
                ["unar", "-quiet", "-force-overwrite", "-no-directory",
                 "-output-directory", str(target), str(source)],
                source,
            )
        elif kind is Kind.COMIC_SEVENZIP:
            _run(["7z", "x", "-y", f"-o{target}", str(source)], source)
        elif kind is Kind.COMIC_ZIP:
            with zipfile.ZipFile(source) as archive:
                archive.extractall(target)
        else:
            raise ConversionError(f"cannot extract {kind}")
        yield target


def _run(command: list[str], source: Path) -> None:
    try:
        proc = run_tool(command, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise ConversionError(f"{command[0]} failed on {source.name}: {err}") from err
    if proc.returncode != 0:
        raise ConversionError(
            f"{command[0]} exited {proc.returncode} on {source.name}: {proc.stderr.strip()[:300]}"
        )


def image_files(root: Path) -> list[Path]:
    """Every image under `root`, in a stable order."""
    files = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in IMAGE_SUFFIXES
        and "__MACOSX" not in path.parts
        and not path.name.startswith("._")
    ]
    return sorted(files, key=lambda p: str(p.relative_to(root)))


def build_cbz(root: Path, dest: Path, comic_info: bytes | None = None) -> Path:
    """Pack images under `root` into a deterministic, stored-only CBZ."""
    images = image_files(root)
    if not images:
        raise ConversionError(f"no images found under {root}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as archive:
        if comic_info is not None:
            archive.writestr(_entry("ComicInfo.xml"), comic_info)
        for image in images:
            arcname = str(image.relative_to(root)).replace("\\", "/")
            archive.writestr(_entry(arcname), image.read_bytes())
    return dest


def _entry(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
    info.compress_type = zipfile.ZIP_STORED
    # Pinned so the archive does not vary with the umask or the host OS.
    info.create_system = _UNIX_CREATOR
    info.external_attr = _MODE_644
    return info


def comic_info_xml(meta: BookMeta) -> bytes:
    """Render ComicInfo.xml so Kavita gets real metadata, not a filename parse."""
    root = ET.Element("ComicInfo", {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
    })
    fields = {
        "Series": meta.series,
        "Number": None if meta.series_index is None else _plain_number(meta.series_index),
        "Title": meta.title,
        "Writer": meta.authors,
    }
    for tag, value in fields.items():
        if value:
            ET.SubElement(root, tag).text = str(value)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _plain_number(index: float) -> str:
    return str(int(index)) if index == int(index) else f"{index:g}"


def convert_to_cbz(source: Path, kind: Kind, dest: Path, meta: BookMeta) -> Path:
    """Extract, then repack deterministically with ComicInfo.xml embedded."""
    with extracted(source, kind) as root:
        return build_cbz(root, dest, comic_info_xml(meta) if meta.is_usable else None)


def copy_into(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest


# --------------------------------------------------------------------------- #
# EPUBs zipped together with their folder
# --------------------------------------------------------------------------- #

_CONTAINER = "META-INF/container.xml"


def nested_epub_root(path: Path) -> str | None:
    """The wrapper folder (``"name/"``) of an EPUB zipped along with its folder.

    OCF requires ``META-INF/container.xml`` at the archive root. An EPUB that was
    zipped as a folder has it one level down instead: calibre then cannot find the
    OPF and invents metadata from the filename, and readers cannot open it at all.
    None for a normal EPUB, and for anything more ambiguous than one wrapper.
    """
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    if _CONTAINER in names:
        return None
    roots = {name.split("/", 1)[0] for name in names if "/" in name}
    wrappers = [root for root in roots if f"{root}/{_CONTAINER}" in names]
    if len(wrappers) != 1:
        return None
    prefix = f"{wrappers[0]}/"
    return prefix if all(name.startswith(prefix) for name in names) else None


def flatten_nested_epub(source: Path, dest: Path) -> Path:
    """Repack without the wrapper folder, as a valid and deterministic EPUB.

    ``mimetype`` goes first and stored, as OCF requires; everything else follows
    in sorted order at the pinned timestamp, for the same reason comics are packed
    that way — the output's partial MD5 is the book's identity, so the same input
    must always produce the same bytes.
    """
    prefix = nested_epub_root(source)
    if prefix is None:
        raise ConversionError(f"not a nested EPUB: {source.name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(dest, "w") as out:
        names = sorted(
            name[len(prefix):] for name in src.namelist()
            if not name.endswith("/") and len(name) > len(prefix)
        )
        if "mimetype" in names:
            names = ["mimetype"] + [n for n in names if n != "mimetype"]
        for name in names:
            info = _entry(name)
            if name != "mimetype":
                info.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(info, src.read(prefix + name))
    return dest
