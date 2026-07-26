"""Two runs over one inbox, and files that never leave it.

Both of these happened in production on the first real ingest. The systemd path
unit fired when a batch was copied in, ran `shelf ingest`, and raced a manual run:
one process deleted a file the other was part-way through hashing. Separately,
files skipped as already-published stayed in the inbox forever, so the path unit
re-triggered on every pass — 74 service starts in twenty minutes.
"""

from __future__ import annotations

from conftest import make_cbz

from shelf.ledger import Ledger
from shelf.pipeline import Action, exclusive, inbox_files, ingest_file
from spinecore.config import shelf_config


def test_only_one_run_can_hold_the_lock(workspace):
    config = shelf_config()
    with exclusive(config) as first:
        assert first is True
        with exclusive(config) as second:
            assert second is False, "a second run must not proceed concurrently"
    # Released once the first block exits.
    with exclusive(config) as third:
        assert third is True


def test_a_file_taken_by_another_run_is_skipped_not_crashed(workspace):
    """The scan lists a path; another process publishes and unlinks it first."""
    config = shelf_config()
    with Ledger(config.ledger) as ledger:
        vanished = config.inbox / "gone.epub"
        outcome = ingest_file(vanished, config, ledger)
    assert outcome.action is Action.SKIPPED
    assert "another run" in outcome.reason


def test_an_already_published_duplicate_leaves_the_inbox(workspace):
    """Otherwise the path unit re-triggers on it forever."""
    config = shelf_config()
    with Ledger(config.ledger) as ledger:
        first = make_cbz(config.inbox / "saga12.cbz")
        assert ingest_file(first, config, ledger).action is Action.PUBLISHED

        # The same bytes under a different name, as a re-download would be.
        duplicate = make_cbz(config.inbox / "saga12-again.cbz")
        outcome = ingest_file(duplicate, config, ledger)

    assert outcome.action is Action.SKIPPED
    assert not duplicate.exists(), "a skipped duplicate must not stay in the inbox"
    assert inbox_files(config) == []


def test_a_duplicate_is_kept_when_the_published_copy_has_gone(workspace):
    """The ledger says we handled these bytes, but the library file is missing.

    Deleting the only remaining copy on the strength of a stale ledger row would
    lose the book outright, so this re-publishes instead.
    """
    config = shelf_config()
    with Ledger(config.ledger) as ledger:
        first = make_cbz(config.inbox / "saga12.cbz")
        published = ingest_file(first, config, ledger).dest
        assert published is not None
        published.unlink()

        again = make_cbz(config.inbox / "saga12-again.cbz")
        outcome = ingest_file(again, config, ledger)

    assert outcome.action is Action.PUBLISHED
    assert outcome.dest is not None and outcome.dest.is_file()
