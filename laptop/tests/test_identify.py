from __future__ import annotations

from conftest import make_cbz, make_epub

from shelf.identify import Kind, identify


def test_epub_is_identified_by_its_mimetype_entry(tmp_path):
    path = make_epub(tmp_path / "book.epub")
    assert identify(path).kind is Kind.EPUB


def test_cbz_is_identified_by_its_payload(tmp_path):
    path = make_cbz(tmp_path / "comic.cbz")
    assert identify(path).kind is Kind.COMIC_ZIP


def test_a_zip_named_cbr_is_still_a_zip(tmp_path):
    """Plenty of .cbr files are ZIPs. Routing on the extension hands a ZIP to a
    RAR extractor and calls the resulting failure "corrupt"."""
    path = make_cbz(tmp_path / "mislabelled.cbr")
    assert identify(path).kind is Kind.COMIC_ZIP


def test_a_rar_named_cbz_is_still_a_rar(tmp_path):
    path = tmp_path / "mislabelled.cbz"
    path.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"\x00" * 200)
    assert identify(path).kind is Kind.COMIC_RAR


def test_rar4_and_rar5_both_route_to_conversion(tmp_path):
    for name, magic in (("v4.cbr", b"Rar!\x1a\x07\x00"), ("v5.cbr", b"Rar!\x1a\x07\x01\x00")):
        path = tmp_path / name
        path.write_bytes(magic + b"\x00" * 200)
        identity = identify(path)
        assert identity.kind is Kind.COMIC_RAR
        assert identity.needs_conversion


def test_seven_zip(tmp_path):
    path = tmp_path / "archive.cb7"
    path.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 200)
    assert identify(path).kind is Kind.COMIC_SEVENZIP


def test_pdf(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"\x00" * 200)
    assert identify(path).kind is Kind.PDF


def test_mobi_is_detected_at_offset_60(tmp_path):
    path = tmp_path / "book.azw3"
    path.write_bytes(b"\x00" * 60 + b"BOOKMOBI" + b"\x00" * 100)
    assert identify(path).kind is Kind.MOBI


def test_a_zip_of_documents_is_not_a_comic(tmp_path):
    import zipfile

    path = tmp_path / "notes.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name in ("a.txt", "b.txt", "c.txt"):
            archive.writestr(name, "text")
    identity = identify(path)
    assert identity.kind is Kind.UNKNOWN
    assert identity.detail


def test_garbage_is_unknown_rather_than_assumed(tmp_path):
    path = tmp_path / "mystery.bin"
    path.write_bytes(b"\x89PNG-ish nonsense" + b"\x00" * 100)
    assert identify(path).kind is Kind.UNKNOWN


def test_macos_resource_forks_do_not_disqualify_a_comic(tmp_path):
    import zipfile

    path = tmp_path / "comic.cbz"
    with zipfile.ZipFile(path, "w") as archive:
        for index in range(4):
            archive.writestr(f"page{index}.jpg", b"x" * 100)
        archive.writestr("__MACOSX/._page0.jpg", b"junk")
        archive.writestr("ComicInfo.xml", "<ComicInfo/>")
    assert identify(path).kind is Kind.COMIC_ZIP
