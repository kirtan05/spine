"""Curated series metadata applied to already-published books.

Refiling is the one sanctioned way to touch a published book, and it is safe only
because doc_hash is derived from contents: a move cannot change a book's identity,
so progress and sessions keyed on that hash are untouched.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from shelf.ledger import Ledger
from shelf.pipeline import ingest_file
from shelf.refile import _normalise, apply_plan, build_plan
from spinecore.config import shelf_config
from spinecore.partial_md5 import partial_md5


def make_seriesless(path: Path, title: str, writer: str, pages: int) -> Path:
    """A book with a title and an author but no series at all.

    conftest's make_cbz ties ComicInfo's presence to the series argument, so it
    cannot express this — and this is precisely the shape refiling exists for:
    139 of 153 books in the real export look exactly like it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "ComicInfo.xml",
            f"<ComicInfo><Title>{title}</Title><Writer>{writer}</Writer></ComicInfo>",
        )
        for index in range(pages):
            archive.writestr(f"page-{index:03d}.jpg", bytes([index]) * 512)
    return path


@pytest.fixture
def library(workspace):
    config = shelf_config()
    with Ledger(config.ledger) as ledger:
        for index, title in enumerate(["Mort", "Small Gods: Discworld Novel, A"], start=1):
            make_seriesless(config.inbox / f"book{index}.cbz", title, "Terry Pratchett", index + 1)
        for path in sorted(config.inbox.iterdir()):
            outcome = ingest_file(path, config, ledger)
            assert outcome.dest is not None, f"fixture failed to publish: {outcome.reason}"
        yield config, ledger


MAPPING = [{"author": "Terry Pratchett", "series": "Discworld",
            "titles": {"Mort": 4, "Small Gods": 13}}]


class TestTitleMatching:
    def test_ignores_subtitles_case_and_punctuation(self):
        assert _normalise("Small Gods: Discworld Novel, A") == _normalise("Small Gods")
        assert _normalise("Raising Steam: (Discworld Novel 40)") == _normalise("raising steam")

    def test_keeps_genuinely_different_titles_apart(self):
        assert _normalise("Night Watch") != _normalise("The Wee Free Men")


def test_plans_a_move_into_the_series_folder(library):
    config, ledger = library
    plans, problems = build_plan(MAPPING, config, ledger)

    assert problems == []
    assert {p.title for p in plans} == {"Mort", "Small Gods"}
    mort = next(p for p in plans if p.title == "Mort")
    assert mort.dest == config.library / "Terry Pratchett" / "Discworld" / "Discworld 04 - Mort.cbz"


def test_the_hash_survives_the_move(library):
    """The whole safety argument in one assertion."""
    config, ledger = library
    plans, _ = build_plan(MAPPING, config, ledger)
    before = {p.doc_hash for p in plans}

    moved, failures = apply_plan(plans, ledger)

    assert failures == []
    assert moved == 2
    after = {partial_md5(p.dest) for p in plans}
    assert after == before, "a move must not change any book's identity"


def test_the_ledger_and_paths_agree_afterwards(library):
    config, ledger = library
    plans, _ = build_plan(MAPPING, config, ledger)
    apply_plan(plans, ledger)

    for entry in ledger.all_published():
        assert entry.series == "Discworld"
        assert entry.series_index in {4.0, 13.0}
        # The recorded path is where the file actually is, not where it was.
        assert Path(entry.published_path).is_file()


def test_a_title_that_matches_nothing_is_reported_not_guessed(library):
    config, ledger = library
    mapping = [{"author": "Terry Pratchett", "series": "Discworld", "titles": {"Thud!": 34}}]

    plans, problems = build_plan(mapping, config, ledger)

    assert plans == []
    assert len(problems) == 1
    assert "no book" in problems[0].reason


def test_refiling_twice_is_a_no_op(library):
    config, ledger = library
    plans, _ = build_plan(MAPPING, config, ledger)
    apply_plan(plans, ledger)

    again, problems = build_plan(MAPPING, config, ledger)
    assert problems == []
    assert all(not p.moves for p in again), "already-filed books must not move again"
    moved, failures = apply_plan(again, ledger)
    assert (moved, failures) == (0, [])
