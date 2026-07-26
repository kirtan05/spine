"""Publishing and quarantine — the two ways a file leaves the pipeline.

**Published files are immutable.** KOReader's Binary matching keys on a partial
MD5 of file contents, so re-tagging or repacking a file after it has been
downloaded to a device forks it into a separate book with separate progress.
Everything upstream of this module exists so that nothing downstream of it ever
has to change a file.

The move is atomic: a partially written file in a Kavita library gets scanned,
indexed, and read as a broken book before it finishes copying.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


class CollisionError(RuntimeError):
    """Destination exists with different contents."""


def publish(payload: Path, dest: Path) -> Path:
    """Atomically place `payload` at `dest`.

    Written to a temporary name in the destination directory first, so the rename
    is same-filesystem and therefore atomic.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".shelf-", suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, open(payload, "rb") as src:
            shutil.copyfileobj(src, out, length=4 * 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    # Durability of the rename itself, so a crash cannot leave a directory entry
    # pointing at nothing.
    dir_fd = os.open(dest.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return dest


def archive_original(source: Path, archive_root: Path, content_hash: str) -> Path:
    """Keep the pre-transformation original in cold storage.

    Only called when the published file is *not* the original — a RAR that became
    a CBZ, or anything the KCC spread pass rewrote. The KCC pass is lossy and
    device lineups change, so throwing the original away is not recoverable.
    """
    dest_dir = archive_root / content_hash[:2]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{content_hash[:16]}-{source.name}"
    if dest.exists():
        return dest
    return publish(source, dest)


def quarantine(source: Path, reason: str, content_hash: str, root: Path) -> Path:
    """Set a file aside with a reason, rather than guessing at it.

    The content hash is appended so two files that collide on name — a common
    reason for landing here in the first place — do not collide again inside
    quarantine.
    """
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in reason.lower())[:60]
    dest_dir = root / (slug or "unknown")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{source.stem}.{content_hash[:12]}{source.suffix}"
    if dest.exists():
        return dest
    return publish(source, dest)
