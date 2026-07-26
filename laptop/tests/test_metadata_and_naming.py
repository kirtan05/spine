from __future__ import annotations

from pathlib import Path

from shelf.metadata import BookMeta, _split_series, parse_comic_filename, parse_comic_info
from shelf.naming import format_index, primary_author, sanitise, target_path

LIBRARY = Path("/library")


class TestComicInfo:
    def test_reads_series_number_and_writer(self):
        meta = parse_comic_info(
            b"<ComicInfo><Series>Saga</Series><Number>12</Number>"
            b"<Title>Chapter Twelve</Title><Writer>Brian K. Vaughan</Writer></ComicInfo>"
        )
        assert meta.series == "Saga"
        assert meta.series_index == 12
        assert meta.title == "Chapter Twelve"
        assert meta.authors == "Brian K. Vaughan"
        assert meta.source == "comicinfo"

    def test_synthesises_a_title_when_the_issue_has_none(self):
        meta = parse_comic_info(b"<ComicInfo><Series>Saga</Series><Number>12</Number></ComicInfo>")
        assert meta.title == "Saga #12"

    def test_handles_a_decimal_issue_number(self):
        meta = parse_comic_info(
            b"<ComicInfo><Series>Saga</Series><Number>12.5</Number></ComicInfo>"
        )
        assert meta.series_index == 12.5

    def test_non_numeric_issue_number_leaves_the_index_unset(self):
        meta = parse_comic_info(
            b"<ComicInfo><Series>Saga</Series><Number>Annual</Number></ComicInfo>"
        )
        assert meta.series == "Saga"
        assert meta.series_index is None

    def test_malformed_xml_yields_nothing_rather_than_raising(self):
        assert not parse_comic_info(b"<ComicInfo><Series>Saga").is_usable


class TestFilenameFallback:
    def test_parses_series_and_issue(self):
        meta = parse_comic_filename("Saga 012 (2013).cbz")
        assert meta.series == "Saga"
        assert meta.series_index == 12
        assert meta.source == "filename"

    def test_parses_a_volume_marker(self):
        assert parse_comic_filename("Berserk v03.cbz").series_index == 3

    def test_refuses_a_bare_title(self):
        # Guessing here silently merges unrelated series in Kavita, with no signal
        # that it happened. Quarantine is the correct outcome.
        assert not parse_comic_filename("some random download.cbz").is_usable

    def test_refuses_a_one_character_series(self):
        assert not parse_comic_filename("A 12.cbz").is_usable


class TestSeriesSplitting:
    def test_splits_calibres_hash_notation(self):
        assert _split_series("Malazan #3") == ("Malazan", 3.0)

    def test_handles_a_decimal_index(self):
        assert _split_series("Cradle #4.5") == ("Cradle", 4.5)

    def test_leaves_a_bare_series_name_alone(self):
        assert _split_series("Malazan") == ("Malazan", None)

    def test_handles_absence(self):
        assert _split_series(None) == (None, None)


class TestNaming:
    def test_series_layout_matches_kavitas_detection_pattern(self):
        meta = BookMeta(
            title="Chapter Twelve", authors="Brian K. Vaughan", series="Saga", series_index=12
        )
        assert target_path(LIBRARY, meta, ".cbz") == Path(
            "/library/Brian K. Vaughan/Saga/Saga 12 - Chapter Twelve.cbz"
        )

    def test_standalone_books_still_get_a_series_shaped_folder(self):
        meta = BookMeta(title="Piranesi", authors="Susanna Clarke")
        assert target_path(LIBRARY, meta, ".epub") == Path(
            "/library/Susanna Clarke/Piranesi/Piranesi.epub"
        )

    def test_files_under_the_first_of_several_authors(self):
        assert primary_author("Steven Erikson & Ian C. Esslemont") == "Steven Erikson"
        # KOReader joins authors with newlines rather than ampersands.
        assert primary_author("Steven Erikson\nIan C. Esslemont") == "Steven Erikson"

    def test_missing_author_gets_a_stable_placeholder(self):
        assert primary_author(None) == "Unknown Author"

    def test_strips_path_separators_and_control_characters(self):
        assert sanitise("Kill/Six\\Billion: Demons") == "KillSixBillion Demons"
        assert sanitise("  trailing dots... ") == "trailing dots"

    def test_zero_pads_so_lexical_order_matches_reading_order(self):
        assert format_index(3) == "03"
        assert format_index(12) == "12"
        assert format_index(112) == "112"
        assert format_index(4.5) == "4.5"
        assert format_index(None) is None

    def test_refuses_to_name_a_book_with_no_usable_metadata(self):
        import pytest

        with pytest.raises(ValueError):
            target_path(LIBRARY, BookMeta(), ".cbz")
