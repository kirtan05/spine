"""The ported hash is the identity of every book in the system. Guard it closely."""

from __future__ import annotations

import hashlib

from spinecore.partial_md5 import SAMPLE_SIZE, partial_md5, sample_offsets

# KOReader's own comment in frontend/util.lua lists the file sizes at which
# appending data can change the digest. That list *is* the sample offset list
# from i = 0 onwards, and it comes from upstream rather than from this port.
KOREADER_DOCUMENTED_OFFSETS = [
    1024, 4096, 16384, 65536, 262144,
    1048576, 4194304, 16777216, 67108864, 268435456, 1073741824,
]


def test_first_offset_is_the_file_head_not_256():
    """`lshift(1024, -2)` is a *left* shift by 30 in LuaJIT, truncated to 32 bits.

    Reading the Lua arithmetically instead of as LuaJIT defines it gives 256, and
    a port that samples at 256 produces a plausible-looking hash that matches
    nothing KOReader ever computed.
    """
    assert sample_offsets()[0] == 0


def test_offsets_match_koreaders_documented_list():
    assert sample_offsets()[1:] == KOREADER_DOCUMENTED_OFFSETS


def test_twelve_samples():
    assert len(sample_offsets()) == 12


def test_hashes_a_short_file_by_the_lua_semantics(tmp_path):
    """Reference vector derived from the Lua, not from this implementation.

    A 5000-byte file is long enough to reach offsets 0, 1024 and 4096, and short
    enough that the seek to 16384 lands past EOF and breaks the loop.
    """
    data = bytes((i * 7 + 3) % 256 for i in range(5000))
    path = tmp_path / "book.epub"
    path.write_bytes(data)

    expected = hashlib.md5(
        data[0:SAMPLE_SIZE] + data[1024 : 1024 + SAMPLE_SIZE] + data[4096:],
        usedforsecurity=False,
    ).hexdigest()

    assert partial_md5(path) == expected


def test_file_shorter_than_one_sample_hashes_its_whole_contents(tmp_path):
    data = b"a short book"
    path = tmp_path / "tiny.cbz"
    path.write_bytes(data)
    assert partial_md5(path) == hashlib.md5(data, usedforsecurity=False).hexdigest()


def test_empty_file_hashes_to_the_empty_digest(tmp_path):
    path = tmp_path / "empty.epub"
    path.write_bytes(b"")
    assert partial_md5(path) == hashlib.md5(b"", usedforsecurity=False).hexdigest()


def test_changing_the_tail_beyond_the_last_sample_does_not_change_the_hash(tmp_path):
    """The head weighting exists so KOReader appending to a PDF is survivable."""
    base = bytes((i * 11) % 256 for i in range(9000))
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    first.write_bytes(base)
    second.write_bytes(base + b"highlight annotations appended by KOReader")

    assert partial_md5(first) == partial_md5(second)


def test_changing_the_head_does_change_the_hash(tmp_path):
    base = bytearray((i * 11) % 256 for i in range(9000))
    first = tmp_path / "a.pdf"
    first.write_bytes(bytes(base))
    base[0] ^= 0xFF
    second = tmp_path / "b.pdf"
    second.write_bytes(bytes(base))

    assert partial_md5(first) != partial_md5(second)


def test_is_stable_across_calls(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(bytes(range(256)) * 40)
    assert partial_md5(path) == partial_md5(path)
