"""Optional KCC pass to pre-rotate two-page spreads.

KOReader has no auto-rotate for these, so a spread renders as an unreadable
sliver on a phone. KCC can split or rotate them ahead of time.

Two hard rules, both from painful reasoning rather than taste:

**One canonical output for every device.** It is tempting to render a phone
profile and a tablet profile. Doing so changes the bytes, which changes the
partial MD5, which forks progress per device — the exact failure this whole system
exists to prevent. One profile, all devices, or none at all.

**Archive the pre-KCC original.** The pass is lossy and device lineups change.

Off by default. Turn it on with SHELF_SPREADS=true once the device lineup is
settled, and understand that it changes every hash it touches — so it must be
decided before books reach a reader, not after.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


class SpreadsUnavailable(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("kcc-c2e") is not None


def process(source: Path, profile: str, dest_dir: Path) -> Path:
    """Run the spread pass, returning the path to the new CBZ.

    Raises SpreadsUnavailable rather than silently skipping: a config that says
    spreads are on, quietly producing un-processed files, is worse than an error.
    """
    if not available():
        raise SpreadsUnavailable(
            "kcc-c2e not found. Install with: uv tool install --python 3.12 KindleComicConverter"
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shelf-kcc-") as tmp:
        out_dir = Path(tmp)
        command = [
            "kcc-c2e",
            "--profile", profile,
            "--format", "CBZ",
            "--splitter", "1",   # rotate spreads rather than cutting them in half
            "--upscale",
            "--output", str(out_dir),
            str(source),
        ]
        proc = subprocess.run(command, capture_output=True, text=True, timeout=3600, check=False)
        if proc.returncode != 0:
            raise SpreadsUnavailable(
                f"kcc-c2e exited {proc.returncode}: {proc.stderr.strip()[:300]}"
            )

        produced = sorted(out_dir.glob("*.cbz"))
        if not produced:
            raise SpreadsUnavailable(f"kcc-c2e produced no output for {source.name}")

        final = dest_dir / produced[0].name
        shutil.move(str(produced[0]), final)
        return final
