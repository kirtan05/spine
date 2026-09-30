"""The wrangler transport inlines values as SQL literals instead of binding them.

That makes `sql_literal` and `inline_params` the security boundary for that path,
so they are tested directly. An apostrophe in a title is not a hypothetical — it
is most of a library.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from spinecore.d1 import D1Error, _rows_from_wrangler, get_client, inline_params, sql_literal


class TestSqlLiteral:
    def test_none_is_null(self):
        assert sql_literal(None) == "NULL"

    def test_numbers_are_bare(self):
        assert sql_literal(12) == "12"
        assert sql_literal(12.5) == "12.5"

    def test_booleans_become_integers(self):
        assert sql_literal(True) == "1"
        assert sql_literal(False) == "0"

    def test_non_finite_numbers_are_refused(self):
        with pytest.raises(D1Error):
            sql_literal(float("nan"))
        with pytest.raises(D1Error):
            sql_literal(float("inf"))

    def test_apostrophes_are_doubled(self):
        assert sql_literal("Locke Lamora's") == "'Locke Lamora''s'"

    def test_a_quote_terminated_injection_does_not_escape_the_literal(self):
        got = sql_literal("'; DROP TABLE documents; --")
        assert got == "'''; DROP TABLE documents; --'"
        # Every quote in the payload is doubled, so the literal never closes early.
        assert got.count("'") % 2 == 0

    def test_newlines_survive(self):
        # KOReader joins multiple authors with newlines.
        assert sql_literal("Erikson\nEsslemont") == "'Erikson\nEsslemont'"

    def test_nul_bytes_are_stripped(self):
        assert sql_literal("a\x00b") == "'ab'"

    def test_bytes_become_a_blob_literal(self):
        assert sql_literal(b"\x00\xff") == "X'00ff'"


class TestInlineParams:
    def test_substitutes_in_order(self):
        got = inline_params("INSERT INTO t VALUES (?, ?)", ["a", 2])
        assert got == "INSERT INTO t VALUES ('a', 2)"

    def test_leaves_question_marks_inside_string_literals_alone(self):
        sql = "SELECT * FROM t WHERE label = 'why?' AND id = ?"
        assert inline_params(sql, [7]) == "SELECT * FROM t WHERE label = 'why?' AND id = 7"

    def test_handles_an_escaped_quote_inside_a_literal(self):
        sql = "SELECT * FROM t WHERE name = 'it''s? fine' AND id = ?"
        assert inline_params(sql, [1]).endswith("id = 1")

    def test_refuses_a_parameter_count_mismatch(self):
        with pytest.raises(D1Error):
            inline_params("VALUES (?, ?)", ["only-one"])
        with pytest.raises(D1Error):
            inline_params("VALUES (?)", ["one", "two"])

    def test_a_value_containing_a_placeholder_is_not_re_scanned(self):
        # The substituted literal must not itself be treated as SQL to parse.
        out = inline_params("VALUES (?, ?)", ["a ? b", 3])
        assert out == "VALUES ('a ? b', 3)"


class TestWranglerOutputParsing:
    def test_skips_npm_and_wrangler_notices(self):
        stdout = (
            "npm notice run wrangler\n"
            " ⛅️ wrangler 4.114.0\n"
            '[{"results":[{"n":3}],"success":true}]\n'
        )
        assert _rows_from_wrangler(stdout) == [{"n": 3}]

    def test_handles_a_bare_object(self):
        assert _rows_from_wrangler('{"results":[{"a":1}]}') == [{"a": 1}]

    def test_empty_output_is_no_rows_rather_than_an_error(self):
        assert _rows_from_wrangler("npm notice only\n") == []

    def test_unparseable_json_raises(self):
        with pytest.raises(D1Error):
            _rows_from_wrangler("[{not json")


class TestTestsNeverReachProduction:
    """Deleting the CF_* variables is not enough: get_client() then falls back to
    the wrangler OAuth session, which writes to the real database. The refile tests
    did exactly that — two fixture books, "Mort" and "Small Gods" as .cbz, sat in
    production `documents` until they were found by hand.
    """

    def test_the_kill_switch_disables_every_transport(self, monkeypatch):
        monkeypatch.setenv("SPINE_D1", "off")
        client, note = get_client()
        assert client is None
        assert "SPINE_D1" in note

    def test_the_whole_suite_runs_with_it_set(self):
        # Set by conftest for every test, not only those using the workspace fixture.
        assert os.environ.get("SPINE_D1") == "off"



def test_dotenv_defaults_to_the_laptop_directory():
    """It pointed one directory too high (the repo root, where no .env exists),
    so laptop/.env was silently never read."""
    from spinecore import config

    assert Path(config.__file__).resolve().parents[2] / ".env" == config.DOTENV
    assert config.DOTENV.parent.name == "laptop"
