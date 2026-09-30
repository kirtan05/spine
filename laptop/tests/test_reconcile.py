"""Whole-library reconciliation against the curated catalogue in mappings/."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from shelf.ledger import Entry, Ledger
from shelf.metadata import BookMeta, epub_metadata
from shelf.pipeline import Action, ingest_file
from shelf.reconcile import (
    Catalogue,
    Problem,
    apply_plans,
    build_plan,
    canonical_authors,
    desired_metadata,
    load_catalogue,
    title_matches,
)
from spinecore.config import shelf_config
from spinecore.partial_md5 import partial_md5
from spinecore.process import run as run_tool

FIXTURE = Path(__file__).parent / "fixtures" / "koreader-verified.epub"
MAPPINGS = Path(__file__).parents[1] / "mappings"
needs_calibre = pytest.mark.skipif(shutil.which("ebook-meta") is None, reason="needs calibre")

CRADLE = {"author": "Will Wight", "series": "Cradle",
          "titles": {"Unsouled": 1, "Soulsmith": 2, "Blackflame": 3, "Reaper": 10}}


def entry(source_name: str = "book.epub") -> Entry:
    return Entry(content_sha256="c", doc_hash="d", source_name=source_name,
                 published_path="/library/x.epub", kind="epub", title=None, authors=None,
                 series=None, series_index=None, metadata_source="ebook-meta",
                 synced_to_d1=1, ingested_at=0)


class TestTitleMatching:
    """Real titles carry the series in every shape; matching has to see through it."""

    @pytest.mark.parametrize("actual", [
        "Cradle 2: Soulsmith", "Soulsmith", "soulsmith (Cradle Book 2)", "02. Soulsmith",
    ])
    def test_finds_the_title_inside_series_decoration(self, actual):
        assert title_matches(actual, "Soulsmith")

    def test_ignores_a_leading_article_and_punctuation(self):
        assert title_matches("Wizard of Earthsea (9780544084377)", "A Wizard of Earthsea")
        assert title_matches("Cursor’s Fury", "Cursor's Fury")
        assert title_matches("[Liz Carlyle 04] • Liz Carlyle - 04 - Dead Line", "Dead Line")

    def test_matches_whole_words_only(self):
        assert not title_matches("Reaper Man", "Reap")
        assert not title_matches("The Mortal Instruments", "Mort")


class TestAuthors:
    ALIASES = {"Le Guin, Ursula K.": "Ursula K. Le Guin", "Carr, Jack": "Jack Carr"}

    def test_maps_a_variant_to_its_canonical_spelling(self):
        assert canonical_authors("Le Guin, Ursula K.", self.ALIASES) == "Ursula K. Le Guin"

    def test_maps_each_author_in_a_list(self):
        got = canonical_authors("Carr, Jack & Le Guin, Ursula K.", self.ALIASES)
        assert got == "Jack Carr & Ursula K. Le Guin"

    def test_leaves_an_unknown_author_alone(self):
        assert canonical_authors("Terry Pratchett", self.ALIASES) == "Terry Pratchett"


class TestDesiredMetadata:
    def test_applies_series_and_canonical_title(self):
        catalogue = Catalogue(series=[CRADLE])
        got = desired_metadata(entry(), BookMeta(title="Cradle 2: Soulsmith",
                                                 authors="Will Wight"), catalogue)
        assert (got.title, got.series, got.series_index) == ("Soulsmith", "Cradle", 2.0)

    def test_series_is_scoped_to_the_author(self):
        catalogue = Catalogue(series=[CRADLE])
        got = desired_metadata(entry(), BookMeta(title="Reaper Man",
                                                 authors="Terry Pratchett"), catalogue)
        assert got.series is None

    def test_aliases_apply_before_series_matching(self):
        catalogue = Catalogue(authors={"Wight, Will": "Will Wight"}, series=[CRADLE])
        got = desired_metadata(entry(), BookMeta(title="03. Blackflame",
                                                 authors="Wight, Will"), catalogue)
        assert (got.authors, got.title, got.series_index) == ("Will Wight", "Blackflame", 3.0)

    def test_a_book_fix_repairs_swapped_title_and_author(self):
        catalogue = Catalogue(
            books={"Jim Butcher.epub": {"title": "First Lord's Fury", "authors": "Jim Butcher"}},
            series=[{"author": "Jim Butcher", "series": "Codex Alera",
                     "titles": {"First Lord's Fury": 6}}],
        )
        current = BookMeta(title="Jim Butcher", authors="Codex Alera 06 - First Lord's Fury")
        got = desired_metadata(entry("Jim Butcher.epub"), current, catalogue)
        assert got.title == "First Lord's Fury"
        assert (got.authors, got.series_index) == ("Jim Butcher", 6.0)

    def test_the_most_specific_title_wins(self):
        catalogue = Catalogue(series=[{"author": "A", "series": "S",
                                       "titles": {"Fury": 1, "First Lord's Fury": 6}}])
        got = desired_metadata(entry(), BookMeta(title="First Lord's Fury", authors="A"), catalogue)
        assert got.series_index == 6.0

    def test_a_genuine_tie_is_reported_not_guessed(self):
        catalogue = Catalogue(series=[
            {"author": "A", "series": "One", "titles": {"Reaper": 1}},
            {"author": "A", "series": "Two", "titles": {"Reaper": 2}},
        ])
        got = desired_metadata(entry(), BookMeta(title="Reaper", authors="A"), catalogue)
        assert isinstance(got, Problem)


def test_the_shipped_catalogue_loads_and_is_consistent():
    catalogue = load_catalogue(MAPPINGS)
    assert catalogue.authors["Ursula K Le Guin"] == "Ursula K. Le Guin"
    assert "Jim Butcher.epub" in catalogue.books
    series = {g["series"] for g in catalogue.series}
    assert {"Cradle", "Discworld", "Codex Alera", "Mage Errant"} <= series
    # Every canonical author a series group names must survive aliasing unchanged,
    # or the group can never match anything.
    for group in catalogue.series:
        if group.get("author"):
            assert canonical_authors(group["author"], catalogue.authors) == group["author"]


# --------------------------------------------------------------------------- #
# End to end, with real calibre
# --------------------------------------------------------------------------- #


def make_book(path: Path, title: str, authors: str, series: str | None = None,
              epub2: bool = False) -> Path:
    """A real EPUB calibre can read, with distinct contents per title."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".staging.epub")  # ebook-meta writes by extension too
    if epub2:
        # calibre stores series differently per OPF version, and can clear it
        # only from an EPUB 2 package.
        with zipfile.ZipFile(FIXTURE) as src, zipfile.ZipFile(staged, "w") as out:
            for info in src.infolist():
                data = src.read(info.filename)
                if info.filename.endswith(".opf"):
                    data = data.replace(b'version="3.0"', b'version="2.0"')
                out.writestr(info, data)
    else:
        shutil.copy2(FIXTURE, staged)
    extra = ["--series", series, "--index", "1"] if series else []
    # Through run_tool: under `uv run`, a bare subprocess resolves calibre's
    # `#!/usr/bin/env python3` to this venv's interpreter and dies on import.
    proc = run_tool(["ebook-meta", str(staged), "--title", title, "--authors", authors, *extra],
                    timeout=120)
    assert proc.returncode == 0, proc.stderr
    staged.rename(path)
    return path


class RecordingClient:
    def __init__(self):
        self.calls: list[tuple[str, list]] = []

    def query(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params or []))
        return []


@pytest.fixture
def library(workspace):
    config = shelf_config()
    with Ledger(config.ledger) as ledger:
        make_book(config.inbox / "Cradle 2_ Soulsmith.pdf", "Cradle 2: Soulsmith", "Wight, Will")
        make_book(config.inbox / "Standalone.epub", "A Standalone", "Someone Else")
        for path in sorted(config.inbox.iterdir()):
            assert ingest_file(path, config, ledger).dest is not None
        catalogue = Catalogue(authors={"Wight, Will": "Will Wight"}, series=[CRADLE])
        yield config, ledger, catalogue


@needs_calibre
class TestReconcileEndToEnd:
    def test_rewrites_moves_and_carries_history(self, library):
        config, ledger, catalogue = library
        before = {e.source_name: e for e in ledger.all_published()}
        old_hash = before["Cradle 2_ Soulsmith.pdf"].doc_hash

        plans, problems = build_plan(ledger, config, catalogue)
        assert problems == []
        client = RecordingClient()
        done, problems = apply_plans(plans, ledger, client)
        assert problems == []

        after = {e.source_name: e for e in ledger.all_published()}
        soulsmith = after["Cradle 2_ Soulsmith.pdf"]
        dest = Path(soulsmith.published_path)
        assert dest == config.library / "Will Wight" / "Cradle" / "Cradle 02 - Soulsmith.epub"
        assert dest.is_file()

        embedded = epub_metadata(dest)
        assert (embedded.title, embedded.authors) == ("Soulsmith", "Will Wight")
        assert embedded.series == "Cradle"
        assert embedded.series_index == 2.0

        assert soulsmith.doc_hash == partial_md5(dest) != old_hash
        assert ("UPDATE progress SET doc_hash = ? WHERE doc_hash = ?",
                [soulsmith.doc_hash, old_hash]) in client.calls
        assert ("DELETE FROM documents WHERE doc_hash = ?", [old_hash]) in client.calls

    def test_a_book_that_already_agrees_is_left_byte_for_byte(self, library):
        config, ledger, catalogue = library
        standalone = next(e for e in ledger.all_published() if e.source_name == "Standalone.epub")
        plans, _ = build_plan(ledger, config, catalogue)
        plan = next(p for p in plans if p.entry.source_name == "Standalone.epub")
        assert not plan.rewrites
        apply_plans(plans, ledger, RecordingClient())
        assert partial_md5(Path(standalone.published_path)) == standalone.doc_hash

    def test_a_second_run_plans_nothing(self, library):
        config, ledger, catalogue = library
        plans, _ = build_plan(ledger, config, catalogue)
        apply_plans(plans, ledger, RecordingClient())

        again, problems = build_plan(ledger, config, catalogue)
        assert problems == []
        assert [p.entry.source_name for p in again if p.changes] == []


@needs_calibre
def test_a_fix_can_clear_a_wrong_series(workspace):
    """The Wheel of Time omnibus arrives tagged 'The Wheel of Time #1'. Writing
    only the fields that are set would leave that tag in place forever, and the
    book would be planned for a rewrite on every run."""
    config = shelf_config()
    with Ledger(config.ledger) as ledger:
        make_book(config.inbox / "Omnibus.epub", "The Omnibus", "Robert Jordan",
                  series="The Wheel of Time", epub2=True)
        assert ingest_file(config.inbox / "Omnibus.epub", config, ledger).dest is not None
        catalogue = Catalogue(books={"Omnibus.epub": {"series": None, "series_index": None}})

        plans, _ = build_plan(ledger, config, catalogue)
        apply_plans(plans, ledger, RecordingClient())

        published = Path(ledger.all_published()[0].published_path)
        assert epub_metadata(published).series is None
        again, _ = build_plan(ledger, config, catalogue)
        assert [p for p in again if p.changes] == []


@needs_calibre
def test_a_write_that_does_not_take_is_reported_and_still_recorded(workspace):
    """calibre cannot clear an EPUB 3 belongs-to-collection. The file has still
    been rewritten, so its new hash must be recorded — but the failure is said out
    loud rather than looping silently on every run."""
    config = shelf_config()
    with Ledger(config.ledger) as ledger:
        make_book(config.inbox / "Omnibus.epub", "The Omnibus", "Robert Jordan",
                  series="The Wheel of Time")
        assert ingest_file(config.inbox / "Omnibus.epub", config, ledger).dest is not None
        catalogue = Catalogue(books={"Omnibus.epub": {"series": None, "series_index": None}})

        plans, _ = build_plan(ledger, config, catalogue)
        _, problems = apply_plans(plans, ledger, RecordingClient())

        assert [p.reason for p in problems if "did not take" in p.reason]
        entry = ledger.all_published()[0]
        assert entry.doc_hash == partial_md5(Path(entry.published_path))


# --------------------------------------------------------------------------- #
# The same catalogue, applied at ingest
# --------------------------------------------------------------------------- #


@needs_calibre
class TestCatalogueAtIngest:
    """Without this, a second edition of a book already in the library gets its
    own path — its OPF lacks the series the first copy was given — and is
    published as a duplicate. Found with a phone download of The Way of Kings."""

    def test_a_new_book_is_published_with_canonical_metadata(self, workspace):
        config = shelf_config()
        catalogue = Catalogue(authors={"Wight, Will": "Will Wight"}, series=[CRADLE])
        make_book(config.inbox / "soulsmith.epub", "Cradle 2: Soulsmith", "Wight, Will")
        with Ledger(config.ledger) as ledger:
            outcome = ingest_file(config.inbox / "soulsmith.epub", config, ledger, catalogue)
        assert outcome.dest == config.library / "Will Wight/Cradle/Cradle 02 - Soulsmith.epub"
        embedded = epub_metadata(outcome.dest)
        assert embedded.title == "Soulsmith"
        assert (embedded.series, embedded.series_index) == ("Cradle", 2.0)

    def test_another_edition_of_an_owned_book_is_quarantined_not_duplicated(self, workspace):
        config = shelf_config()
        catalogue = Catalogue(authors={"Wight, Will": "Will Wight"}, series=[CRADLE])
        with Ledger(config.ledger) as ledger:
            make_book(config.inbox / "a.epub", "Cradle 2: Soulsmith", "Will Wight")
            assert ingest_file(config.inbox / "a.epub", config, ledger, catalogue).dest
            make_book(config.inbox / "b.epub", "Soulsmith (Cradle Book 2)", "Wight, Will")
            outcome = ingest_file(config.inbox / "b.epub", config, ledger, catalogue)
            assert outcome.action is Action.QUARANTINED
            assert len(ledger.all_published()) == 1

    def test_a_marketing_subtitle_is_dropped(self):
        got = desired_metadata(entry(), BookMeta(title="The Understudy: A Novel",
                                                 authors="David Nicholls"), Catalogue())
        assert got.title == "The Understudy"
