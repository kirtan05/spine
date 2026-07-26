"""External tools must not inherit this project's virtualenv.

`shelf` runs under `uv run`, which puts laptop/.venv/bin first on PATH. calibre's
ebook-meta starts with `#!/usr/bin/env python3`, so under that PATH it resolves to
the venv interpreter, which has none of calibre's dependencies. It then dies on an
import several frames deep — and the pipeline records that as "this book has
broken metadata" and quarantines it. Every EPUB in the library, with a plausible
reason attached.
"""

from __future__ import annotations

import os
import sys

from spinecore.process import clean_env, run


def test_removes_virtual_env(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/.venv")
    assert "VIRTUAL_ENV" not in clean_env()


def test_removes_the_venv_bin_from_path(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/.venv")
    monkeypatch.setenv("PATH", os.pathsep.join(["/somewhere/.venv/bin", "/usr/bin", "/bin"]))

    path = clean_env()["PATH"].split(os.pathsep)

    assert "/somewhere/.venv/bin" not in path
    # The rest of PATH survives, or ebook-meta becomes unfindable instead.
    assert "/usr/bin" in path and "/bin" in path


def test_removes_python_path_overrides(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/injected")
    monkeypatch.setenv("PYTHONHOME", "/injected")
    env = clean_env()
    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env


def test_leaves_a_venv_free_environment_alone(monkeypatch):
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert clean_env()["PATH"] == "/usr/bin:/bin"


def test_a_subprocess_no_longer_resolves_the_venv_interpreter():
    """The actual regression: `python3` off PATH must not be the venv's."""
    proc = run([sys.executable, "-c", "import os; print(os.environ.get('VIRTUAL_ENV', 'unset'))"],
               timeout=30)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "unset"
