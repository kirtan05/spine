"""Turn KOReader's per-page timings into sessions.

KOReader records one row per page view: which page, when it started, how long it
was on screen. A reading session is a run of those with no long gap between them.

`duration_s` is the **sum of measured page durations**, not the wall-clock span.
A session where you read for ten minutes, put the tablet down for four, and read
for another ten is twenty minutes of reading inside a twenty-four minute span, and
the honest number is twenty. `ended_at` still carries the span so nothing is lost.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: KOReader's own statistics screen uses a comparable idle cutoff. Anything much
#: shorter splits a single sitting into fragments every time you pause to think.
DEFAULT_GAP_SECONDS = 300


@dataclass(frozen=True)
class PageEvent:
    page: int
    start_time: int
    duration: int


@dataclass(frozen=True)
class Session:
    started_at: int
    ended_at: int
    duration_s: int
    pages: int


def group_sessions(
    events: list[PageEvent], gap_seconds: int = DEFAULT_GAP_SECONDS
) -> list[Session]:
    """Group page events into sessions, splitting on gaps longer than `gap_seconds`."""
    ordered = sorted(events, key=lambda e: (e.start_time, e.page))
    sessions: list[Session] = []
    current: list[PageEvent] = []

    for event in ordered:
        if current:
            previous = current[-1]
            previous_end = previous.start_time + max(0, previous.duration)
            if event.start_time - previous_end > gap_seconds:
                sessions.append(_close(current))
                current = []
        current.append(event)

    if current:
        sessions.append(_close(current))
    return sessions


def _close(events: list[PageEvent]) -> Session:
    return Session(
        started_at=events[0].start_time,
        ended_at=max(e.start_time + max(0, e.duration) for e in events),
        duration_s=sum(max(0, e.duration) for e in events),
        # Distinct pages: re-reading page 40 three times is one page of progress,
        # even though it is three events and three durations.
        pages=len({e.page for e in events}),
    )


def session_id(source: str, device_id: str, doc_hash: str, started_at: int) -> str:
    """Deterministic id derived from the natural key.

    Random UUIDs and "re-running an importer produces no duplicates" contradict
    each other. Deriving the id from (source, device, book, start) makes a re-run
    a no-op by construction rather than by a de-duplication pass that has to be
    correct forever.
    """
    material = f"{source}|{device_id}|{doc_hash}|{started_at}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
