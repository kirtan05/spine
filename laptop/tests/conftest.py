from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

COMIC_INFO = """<?xml version="1.0" encoding="utf-8"?>
<ComicInfo>
  <Series>{series}</Series>
  <Number>{number}</Number>
  <Title>{title}</Title>
  <Writer>{writer}</Writer>
</ComicInfo>
"""


def make_cbz(
    path: Path,
    pages: int = 3,
    series: str | None = "Saga",
    number: str = "12",
    title: str = "The One With The Rocket",
    writer: str = "Brian K. Vaughan",
) -> Path:
    """A CBZ with plausible page images and optional ComicInfo.xml."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        if series is not None:
            archive.writestr(
                "ComicInfo.xml",
                COMIC_INFO.format(series=series, number=number, title=title, writer=writer),
            )
        for index in range(pages):
            archive.writestr(f"page-{index:03d}.jpg", bytes([index]) * 512)
    return path


def make_epub(path: Path, title: str = "Gardens of the Moon") -> Path:
    """Structurally a valid EPUB — enough for identification, not for calibre."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "OEBPS/content.opf",
            f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf">'
            f"<metadata><title>{title}</title></metadata></package>",
        )
        archive.writestr("OEBPS/chapter1.xhtml", "<html><body><p>Hello</p></body></html>")
    return path


@pytest.fixture(autouse=True)
def no_production_d1(monkeypatch):
    """Every test, not just the workspace ones: get_client() would otherwise fall
    back to the wrangler OAuth session and write to the real database."""
    monkeypatch.setenv("SPINE_D1", "off")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """An isolated inbox/library/quarantine/archive tree with matching config."""
    for name in ("inbox", "library", "quarantine", "archive", "data"):
        (tmp_path / name).mkdir()

    monkeypatch.setenv("SHELF_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("SHELF_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setenv("SHELF_QUARANTINE", str(tmp_path / "quarantine"))
    monkeypatch.setenv("SHELF_ARCHIVE", str(tmp_path / "archive"))
    monkeypatch.setenv("SHELF_LEDGER", str(tmp_path / "data" / "ledger.sqlite3"))
    monkeypatch.setenv("SPINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHELF_SPREADS", "false")
    # Never let a test reach the real Cloudflare account.
    for key in ("CF_ACCOUNT_ID", "CF_D1_DATABASE_ID", "CF_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    return tmp_path
