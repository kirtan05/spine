from __future__ import annotations

from kostats.sessions import PageEvent, group_sessions, session_id

BASE = 1_700_000_000


def event(offset: int, duration: int, page: int = 1) -> PageEvent:
    return PageEvent(page=page, start_time=BASE + offset, duration=duration)


class TestGrouping:
    def test_no_events_no_sessions(self):
        assert group_sessions([]) == []

    def test_contiguous_page_turns_are_one_session(self):
        events = [event(0, 60, 1), event(60, 60, 2), event(120, 60, 3)]
        sessions = group_sessions(events)
        assert len(sessions) == 1
        assert sessions[0].duration_s == 180
        assert sessions[0].pages == 3
        assert sessions[0].started_at == BASE
        assert sessions[0].ended_at == BASE + 180

    def test_a_long_gap_splits_the_session(self):
        events = [event(0, 60, 1), event(60, 60, 2), event(3600, 60, 3)]
        sessions = group_sessions(events, gap_seconds=300)
        assert len(sessions) == 2
        assert [s.pages for s in sessions] == [2, 1]

    def test_a_gap_shorter_than_the_threshold_does_not_split(self):
        # Pausing to think is not the end of a reading session.
        events = [event(0, 60, 1), event(60 + 200, 60, 2)]
        assert len(group_sessions(events, gap_seconds=300)) == 1

    def test_duration_counts_measured_time_not_wall_clock(self):
        """Ten minutes read, four minutes idle, ten minutes read: twenty minutes."""
        events = [event(0, 600, 1), event(840, 600, 2)]
        sessions = group_sessions(events, gap_seconds=300)
        assert len(sessions) == 1
        assert sessions[0].duration_s == 1200
        # The span is still recorded, so nothing is lost.
        assert sessions[0].ended_at - sessions[0].started_at == 1440

    def test_rereading_a_page_counts_once(self):
        events = [event(0, 60, 40), event(60, 60, 40), event(120, 60, 40)]
        sessions = group_sessions(events)
        assert sessions[0].pages == 1
        assert sessions[0].duration_s == 180

    def test_unsorted_input_is_handled(self):
        events = [event(120, 60, 3), event(0, 60, 1), event(60, 60, 2)]
        sessions = group_sessions(events)
        assert len(sessions) == 1
        assert sessions[0].started_at == BASE

    def test_negative_durations_are_ignored_rather_than_subtracted(self):
        sessions = group_sessions([event(0, 60, 1), event(60, -5, 2)])
        assert sessions[0].duration_s == 60


class TestDeterministicIds:
    def test_the_same_session_always_gets_the_same_id(self):
        first = session_id("koreader", "pixel", "abc123", BASE)
        second = session_id("koreader", "pixel", "abc123", BASE)
        assert first == second

    def test_each_component_changes_the_id(self):
        base = session_id("koreader", "pixel", "abc123", BASE)
        assert session_id("playbooks", "pixel", "abc123", BASE) != base
        assert session_id("koreader", "tab-s11", "abc123", BASE) != base
        assert session_id("koreader", "pixel", "def456", BASE) != base
        assert session_id("koreader", "pixel", "abc123", BASE + 1) != base

    def test_ids_are_fixed_width_hex(self):
        value = session_id("koreader", "pixel", "abc123", BASE)
        assert len(value) == 32
        assert all(c in "0123456789abcdef" for c in value)
