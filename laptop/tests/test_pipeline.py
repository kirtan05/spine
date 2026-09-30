"""End-to-end pipeline behaviour, against the PRD's acceptance criteria."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from conftest import make_cbz, make_epub

from shelf.convert import build_cbz, comic_info_xml
from shelf.ledger import Ledger
from shelf.metadata import BookMeta
from shelf.pipeline import Action, inbox_files, ingest_file, ingest_paths, prune_inbox
from spinecore.config import shelf_config
from spinecore.partial_md5 import content_sha256, partial_md5


@pytest.fixture
def ctx(workspace):
    config = shelf_config()
    with Ledger(config.ledger) as ledger:
        yield config, ledger, workspace


class TestPublishing:
    def test_a_tagged_comic_lands_where_kavita_expects_it(self, ctx):
        config, ledger, root = ctx
        source = make_cbz(config.inbox / "saga12.cbz")

        outcome = ingest_file(source, config, ledger)

        assert outcome.action is Action.PUBLISHED
        assert outcome.dest == (
            config.library / "Brian K. Vaughan" / "Saga" / "Saga 12 - The One With The Rocket.cbz"
        )
        assert outcome.dest.is_file()
        assert outcome.doc_hash == partial_md5(outcome.dest)

    def test_the_inbox_is_drained(self, ctx):
        config, ledger, _ = ctx
        source = make_cbz(config.inbox / "saga12.cbz")
        ingest_file(source, config, ledger)
        assert not source.exists()
        assert inbox_files(config) == []

    def test_the_pre_conversion_original_is_archived(self, ctx):
        config, ledger, _ = ctx
        source = make_cbz(config.inbox / "saga12.cbz")
        original_hash = content_sha256(source)

        ingest_file(source, config, ledger)

        archived = list(config.archive.rglob("*.cbz"))
        assert len(archived) == 1
        assert content_sha256(archived[0]) == original_hash

    def test_the_published_comic_carries_comicinfo(self, ctx):
        config, ledger, _ = ctx
        outcome = ingest_file(make_cbz(config.inbox / "saga12.cbz"), config, ledger)
        with zipfile.ZipFile(outcome.dest) as archive:
            assert "ComicInfo.xml" in archive.namelist()
            assert b"<Series>Saga</Series>" in archive.read("ComicInfo.xml")

    def test_published_comics_are_stored_never_recompressed(self, ctx):
        config, ledger, _ = ctx
        outcome = ingest_file(make_cbz(config.inbox / "saga12.cbz"), config, ledger)
        with zipfile.ZipFile(outcome.dest) as archive:
            assert {i.compress_type for i in archive.infolist()} == {zipfile.ZIP_STORED}


class TestIdempotency:
    def test_re_running_over_the_same_input_publishes_nothing_new(self, ctx):
        config, ledger, _ = ctx
        make_cbz(config.inbox / "saga12.cbz")
        first = ingest_paths(inbox_files(config), config, ledger)
        assert [o.action for o in first] == [Action.PUBLISHED]

        make_cbz(config.inbox / "saga12.cbz")  # same bytes, dropped in again
        second = ingest_paths(inbox_files(config), config, ledger)

        assert [o.action for o in second] == [Action.SKIPPED]
        assert len(list(config.library.rglob("*.cbz"))) == 1

    def test_the_published_hash_does_not_change_across_runs(self, ctx):
        config, ledger, _ = ctx
        first = ingest_file(make_cbz(config.inbox / "saga12.cbz"), config, ledger)
        before = partial_md5(first.dest)

        # A fresh ledger, as if the laptop were rebuilt.
        with Ledger(config.data_dir / "ledger2.sqlite3") as fresh:
            second = ingest_file(make_cbz(config.inbox / "saga12.cbz"), config, fresh)

        assert second.action is Action.SKIPPED
        assert partial_md5(first.dest) == before

    def test_conversion_is_byte_for_byte_deterministic(self, tmp_path):
        """`zip -0` embeds mtimes, so two runs produce two different books."""
        pages = tmp_path / "pages"
        pages.mkdir()
        for index in range(4):
            (pages / f"page{index}.jpg").write_bytes(bytes([index]) * 400)
        info = comic_info_xml(BookMeta(series="Saga", series_index=12, title="X"))

        first = build_cbz(pages, tmp_path / "a.cbz", info)
        second = build_cbz(pages, tmp_path / "b.cbz", info)

        assert first.read_bytes() == second.read_bytes()
        assert partial_md5(first) == partial_md5(second)


class TestQuarantine:
    def test_a_comic_with_no_metadata_is_never_guessed_at(self, ctx):
        config, ledger, _ = ctx
        source = make_cbz(config.inbox / "some random download.cbz", series=None)

        outcome = ingest_file(source, config, ledger)

        assert outcome.action is Action.QUARANTINED
        assert "no-usable-metadata" in outcome.reason
        assert outcome.dest.is_file()
        assert list(config.library.rglob("*")) == []

    def test_an_unrecognised_format_is_quarantined_with_a_reason(self, ctx):
        config, ledger, _ = ctx
        source = config.inbox / "mystery.bin"
        source.write_bytes(b"not a book at all" * 40)

        outcome = ingest_file(source, config, ledger)

        assert outcome.action is Action.QUARANTINED
        assert "unrecognised-format" in outcome.reason

    def test_a_name_collision_is_quarantined_rather_than_overwriting(self, ctx):
        config, ledger, _ = ctx
        ingest_file(make_cbz(config.inbox / "saga12.cbz", pages=3), config, ledger)

        # Same series and issue, different contents — a genuinely ambiguous case.
        clash = make_cbz(config.inbox / "saga12-alt.cbz", pages=7)
        outcome = ingest_file(clash, config, ledger)

        assert outcome.action is Action.QUARANTINED
        assert outcome.reason == "name-collision"

    def test_quarantined_files_are_suffixed_so_they_cannot_collide_again(self, ctx):
        config, ledger, _ = ctx
        first = ingest_file(make_cbz(config.inbox / "x.cbz", series=None, pages=2), config, ledger)
        second = ingest_file(make_cbz(config.inbox / "x.cbz", series=None, pages=5), config, ledger)

        assert first.dest != second.dest
        assert first.dest.is_file() and second.dest.is_file()


class TestCatalogue:
    def test_published_books_are_pending_catalogue_sync(self, ctx):
        config, ledger, _ = ctx
        ingest_file(make_cbz(config.inbox / "saga12.cbz"), config, ledger)

        counts = ledger.counts()
        assert counts["published"] == 1
        # Every published file needs a documents row before a device opens it, or
        # it renders as "Unknown". Until then it is tracked as outstanding.
        assert counts["unsynced"] == 1

    def test_the_ledger_records_what_the_metadata_came_from(self, ctx):
        config, ledger, _ = ctx
        outcome = ingest_file(make_cbz(config.inbox / "saga12.cbz"), config, ledger)
        entry = ledger.published(content_sha256(next(config.archive.rglob("*.cbz"))))
        assert entry is not None
        assert entry.metadata_source == "comicinfo"
        assert entry.doc_hash == outcome.doc_hash


class TestSyncthingFedInbox:
    """The phone's downloads arrive through Syncthing into ~/inbox/phone, which
    carries Syncthing's own bookkeeping: `.stfolder/` with a marker file inside,
    `.stignore`, and `.syncthing.*.tmp` while a transfer is in flight."""

    def _syncthing_folder(self, inbox: Path) -> Path:
        phone = inbox / "phone"
        (phone / ".stfolder").mkdir(parents=True)
        (phone / ".stfolder" / "syncthing-folder-abc123.txt").write_text("marker")
        (phone / ".stignore").write_text("*\n")
        (phone / ".syncthing.Book.epub.tmp").write_bytes(b"partial")
        return phone

    def test_ignores_everything_inside_a_hidden_directory(self, workspace):
        config = shelf_config()
        phone = self._syncthing_folder(config.inbox)
        book = make_epub(phone / "Book.epub")
        assert inbox_files(config) == [book]

    def test_pruning_never_removes_the_syncthing_folder(self, workspace):
        # Even empty: without .stfolder Syncthing stops the folder outright
        # ("folder marker missing") rather than syncing into a bare directory.
        config = shelf_config()
        phone = self._syncthing_folder(config.inbox)
        (phone / ".stfolder" / "syncthing-folder-abc123.txt").unlink()
        prune_inbox(config)
        assert (phone / ".stfolder").is_dir()
