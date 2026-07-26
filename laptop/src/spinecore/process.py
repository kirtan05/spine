"""Running external tools from inside a virtualenv.

`shelf` runs under ``uv run`` — both by hand and from the systemd path unit — which
puts ``laptop/.venv/bin`` first on PATH and sets ``VIRTUAL_ENV``.

calibre's ``ebook-meta`` starts with ``#!/usr/bin/env python3``. Under that PATH it
resolves to the project venv's interpreter, which has none of calibre's
dependencies, and dies with ``ModuleNotFoundError: No module named 'msgpack'``
part-way through an import chain. The failure is indistinguishable from "this
book has broken metadata", so every single EPUB lands in quarantine with a
plausible-looking reason.

Every external tool therefore gets an environment with this virtualenv removed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_PYTHON_VARS = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE")


def clean_env() -> dict[str, str]:
    """A copy of the environment with this project's virtualenv taken out."""
    env = dict(os.environ)
    venv = env.pop("VIRTUAL_ENV", None)
    for name in _PYTHON_VARS:
        env.pop(name, None)

    if venv:
        bin_dir = str(Path(venv) / "bin")
        parts = [p for p in env.get("PATH", "").split(os.pathsep) if p and p != bin_dir]
        env["PATH"] = os.pathsep.join(parts)
    return env


def run(
    command: list[str], *, timeout: float, cwd: str | Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run an external tool outside the virtualenv, capturing output."""
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd) if cwd is not None else None,
        env=clean_env(),
        check=False,
    )
