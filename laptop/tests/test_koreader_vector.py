"""The reference vector, verified against a real device.

`tests/fixtures/koreader-verified.epub` was opened in KOReader v2026.03 on a Pixel
8. KOReader wrote its own computation of the document hash into the book's sidecar
at `spine-setup.sdr/metadata.epub.lua`:

    ["partial_md5_checksum"] = "6a40b608f5a4b295d35624e493703607"

That value is the ground truth here. It was produced by KOReader, on a device, over
these exact bytes — not by this implementation, and not by reading the Lua and
reasoning about it.

This matters because `partial_md5` is the primary key of every book in the system.
A port that is subtly wrong does not fail loudly: it produces a plausible hash that
simply matches nothing KOReader ever computed, so every device silently forks every
book into a second identity with its own progress and its own history.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from spinecore.partial_md5 import SAMPLE_SIZE, partial_md5

FIXTURE = Path(__file__).parent / "fixtures" / "koreader-verified.epub"

#: Read off a Pixel 8 running KOReader v2026.03.
KOREADER_ON_DEVICE = "6a40b608f5a4b295d35624e493703607"


def test_matches_what_koreader_computed_on_a_real_device():
    assert partial_md5(FIXTURE) == KOREADER_ON_DEVICE


def test_the_naive_first_offset_would_not_match():
    """Guards the specific mistake this port exists to avoid.

    `lshift(1024, -2)` is LuaJIT's bit.lshift, which masks the shift count to five
    bits — a *left* shift by 30, truncated to 32 bits, giving offset 0. Read as
    ordinary arithmetic it looks like a right shift by 2, giving 256.

    If this assertion ever fails, the two implementations have converged and the
    test above has stopped proving anything.
    """
    data = FIXTURE.read_bytes()
    naive = hashlib.md5(
        data[256 : 256 + SAMPLE_SIZE] + data[1024 : 1024 + SAMPLE_SIZE],
        usedforsecurity=False,
    ).hexdigest()
    assert naive != KOREADER_ON_DEVICE


#: Full content digest of the fixture as the device saw it.
FIXTURE_SHA256 = "83d8399e7bb1d2b3b126d97b201fcc67354093bbc04476e2255f2553ff0fb46b"


def test_the_fixture_is_the_bytes_the_device_saw():
    """A silently edited fixture would invalidate the vector without failing it.

    The expected hash above is only meaningful for these exact bytes, so the bytes
    are pinned too.
    """
    assert FIXTURE.stat().st_size == 2388
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
