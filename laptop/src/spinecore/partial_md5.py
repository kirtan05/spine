"""KOReader's document identity hash.

This is a direct port of ``util.partialMD5`` from the KOReader tree, and it has to
stay one. The value it produces is the primary key of every book in the system, so
a port that is subtly wrong does not fail loudly — it silently forks every book
into a second identity with its own progress and its own history.

The Lua, from ``frontend/util.lua``::

    local step, size = 1024, 1024
    local update = md5()
    for i = -1, 10 do
        file:seek("set", lshift(step, 2*i))
        local sample = file:read(size)
        if sample then update(sample) else break end
    end

The trap is ``i = -1``. ``lshift`` is LuaJIT's ``bit.lshift``, which masks the
shift count to five bits, so ``lshift(1024, -2)`` is not a right shift by 2 — it is
a left shift by ``(-2) & 31 == 30``, and ``1024 << 30`` truncated to 32 bits is
**0**. The first sample is therefore the head of the file, not offset 256.

KOReader's own comment corroborates this: it lists the sizes at which appending
data can change the digest as 1024, 4096, 16384 ... and pointedly does not list
256.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

SAMPLE_SIZE = 1024
_STEP = 1024
_UINT32 = 0xFFFFFFFF


def sample_offsets() -> list[int]:
    """The twelve byte offsets KOReader samples, in order.

    Exposed rather than inlined so the ported shift arithmetic is directly
    testable against the offsets KOReader documents.
    """
    return [(_STEP << ((2 * i) & 31)) & _UINT32 for i in range(-1, 11)]


def partial_md5(path: str | Path) -> str:
    """Return KOReader's ``doc_hash`` for a file, as lowercase hex.

    Samples 1 KiB at exponentially spaced offsets, weighted towards the head of
    the file. The weighting exists because KOReader appends to PDFs when
    highlighting, and a head-weighted digest survives that.
    """
    digest = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as handle:
        for offset in sample_offsets():
            handle.seek(offset)
            sample = handle.read(SAMPLE_SIZE)
            # Lua's file:read returns nil past EOF and the loop breaks. An empty
            # read here is the same condition — and it must break, not continue,
            # or a short file would hash differently from KOReader's version.
            if not sample:
                break
            digest.update(sample)
    return digest.hexdigest()


def content_sha256(path: str | Path) -> str:
    """Full-content hash, used for pipeline idempotency.

    Deliberately distinct from ``partial_md5``: that one answers "is this the same
    book to a reader", this one answers "have I already processed these exact
    bytes".
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
