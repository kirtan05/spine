"""Regressions found by running a real Google Play Books export through the pipeline.

Both of these produced output that looked fine in the aggregate — files published,
nothing quarantined — and were only visible in the resulting paths.
"""

from __future__ import annotations

from pathlib import Path

from conftest import make_cbz, make_epub

from shelf.identify import Kind, identify, published_suffix
from shelf.metadata import BookMeta, clean_authors
from shelf.naming import primary_author, target_path

LIBRARY = Path("/library")


class TestAuthorSortSuffix:
    """calibre prints `Will Wight [Wight, Will]`. The bracket is not part of the name."""

    def test_strips_the_bracketed_sort_form(self):
        assert clean_authors("Will Wight [Wight, Will]") == "Will Wight"

    def test_strips_it_from_every_author(self):
        got = clean_authors("Will Wight [Wight, Will] & Jim Butcher [Butcher, Jim]")
        assert got == "Will Wight & Jim Butcher"

    def test_leaves_a_plain_name_alone(self):
        assert clean_authors("Terry Pratchett") == "Terry Pratchett"

    def test_treats_calibres_literal_unknown_as_absent(self):
        assert clean_authors("Unknown") is None
        assert clean_authors("Unknown & Unknown") is None

    def test_the_directory_name_is_the_author_not_half_the_sort_form(self):
        # Left unstripped, the naming step's comma split turns this into
        # "Will Wight [Wight" and files the whole Cradle series under it.
        meta = BookMeta(title="Unsouled", authors=clean_authors("Will Wight [Wight, Will]"))
        assert target_path(LIBRARY, meta, ".epub") == Path(
            "/library/Will Wight/Unsouled/Unsouled.epub"
        )

    def test_primary_author_survives_a_stripped_multi_author_string(self):
        cleaned = clean_authors("A Name [Name, A] & Other One [One, Other]")
        assert primary_author(cleaned) == "A Name"


class TestPublishedExtension:
    """71 of 156 files in the real export are named .pdf and are all EPUBs."""

    def test_an_epub_named_pdf_is_published_as_epub(self, tmp_path):
        path = make_epub(tmp_path / "Mightier than the Sword.pdf")
        identity = identify(path)
        assert identity.kind is Kind.EPUB
        assert published_suffix(identity, path) == ".epub"

    def test_a_real_pdf_stays_pdf(self, tmp_path):
        path = tmp_path / "paper.pdf"
        path.write_bytes(b"%PDF-1.7\n" + b"\x00" * 200)
        assert published_suffix(identify(path), path) == ".pdf"

    def test_a_comic_is_always_cbz_whatever_it_arrived_as(self, tmp_path):
        path = make_cbz(tmp_path / "issue.cbr")
        assert published_suffix(identify(path), path) == ".cbz"

    def test_the_mobi_family_keeps_its_own_container_extension(self, tmp_path):
        path = tmp_path / "book.azw3"
        path.write_bytes(b"\x00" * 60 + b"BOOKMOBI" + b"\x00" * 100)
        assert published_suffix(identify(path), path) == ".azw3"


def test_an_epub_named_pdf_lands_in_the_library_as_epub(tmp_path, monkeypatch):
    """End to end: the published path must not carry the lying extension."""
    from shelf.ledger import Ledger
    from shelf.pipeline import Action, ingest_file
    from spinecore.config import shelf_config

    for name in ("inbox", "library", "quarantine", "archive", "data"):
        (tmp_path / name).mkdir()
    monkeypatch.setenv("SHELF_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("SHELF_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setenv("SHELF_QUARANTINE", str(tmp_path / "quarantine"))
    monkeypatch.setenv("SHELF_ARCHIVE", str(tmp_path / "archive"))
    monkeypatch.setenv("SHELF_LEDGER", str(tmp_path / "data" / "ledger.sqlite3"))
    monkeypatch.setenv("SPINE_DATA_DIR", str(tmp_path / "data"))
    for key in ("CF_ACCOUNT_ID", "CF_D1_DATABASE_ID", "CF_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    config = shelf_config()
    source = make_epub(config.inbox / "Uncrowned.pdf")

    with Ledger(config.ledger) as ledger:
        outcome = ingest_file(source, config, ledger)

    # calibre may or may not be installed in the test environment; either way the
    # extension decision must come from the magic bytes.
    if outcome.action is Action.PUBLISHED:
        assert outcome.dest is not None
        assert outcome.dest.suffix == ".epub"


class TestSortOrderAuthors:
    """Some exports carry the author already in sort order: `Jordan, Robert`."""

    def test_flips_when_calibre_says_the_name_is_its_own_sort_form(self):
        assert clean_authors("Jordan, Robert [Jordan, Robert]") == "Robert Jordan"

    def test_does_not_flip_when_the_display_name_is_already_correct(self):
        assert clean_authors("Will Wight [Wight, Will]") == "Will Wight"

    def test_does_not_mangle_two_authors_separated_by_a_comma(self):
        # No bracket, so no signal that this is sort order — and flipping it would
        # produce one mangled name out of two real ones.
        assert clean_authors("Terry Pratchett, Neil Gaiman") == "Terry Pratchett, Neil Gaiman"

    def test_leaves_a_multi_comma_name_alone(self):
        assert clean_authors("Smith, John, Jr. [Smith, John, Jr.]") == "Smith, John, Jr."

    def test_the_wheel_of_time_files_under_the_authors_real_name(self):
        authors = clean_authors("Jordan, Robert [Jordan, Robert]")
        meta = BookMeta(title="The Wheel of Time", authors=authors)
        assert target_path(LIBRARY, meta, ".epub") == Path(
            "/library/Robert Jordan/The Wheel of Time/The Wheel of Time.epub"
        )


class TestRealComicFilenames:
    """Scene releases carry scanner and quality tags after the issue number."""

    def test_parses_a_release_with_trailing_tags(self):
        from shelf.metadata import parse_comic_filename

        meta = parse_comic_filename("Alias 002 (2001) (Digital) (Zone-Empire).cbr")
        assert meta.series == "Alias"
        assert meta.series_index == 2

    def test_still_refuses_a_bare_trailing_number(self):
        # "Fahrenheit 451" is a title, not issue 451 of a series called Fahrenheit.
        from shelf.metadata import parse_comic_filename

        assert not parse_comic_filename("Fahrenheit 451.cbr").is_usable
        assert not parse_comic_filename("Watchmen 5.cbr").is_usable

    def test_does_not_repeat_a_synthesised_title_in_the_filename(self):
        from shelf.metadata import parse_comic_filename

        meta = parse_comic_filename("Alias 002 (2001) (Digital).cbr")
        # Would otherwise read "Alias 02 - Alias #2.cbz".
        assert target_path(LIBRARY, meta, ".cbz").name == "Alias 02.cbz"

    def test_a_real_title_is_still_kept(self):
        meta = BookMeta(title="The One With The Rocket", series="Saga", series_index=12)
        assert target_path(LIBRARY, meta, ".cbz").name == "Saga 12 - The One With The Rocket.cbz"
