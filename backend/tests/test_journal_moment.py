"""Where a flagged paper or study source sits in the day it was filed under."""
import time

from backend.day_boundary import day_bounds, day_key_for
from backend.journal_moment import journal_moment


def _day(ts: int) -> tuple[int, int]:
    return day_bounds(day_key_for(ts))


def test_an_edit_inside_the_filed_day_is_kept_as_is():
    flagged = int(time.time())
    start, end = _day(flagged)
    edited = start + 9 * 3600
    assert journal_moment(edited, flagged) == edited


def test_the_last_edit_wins_over_the_flag_moment():
    """The whole point: flagging is a filing gesture, usually made on opening."""
    start, _ = _day(int(time.time()))
    flagged = start + 1 * 3600          # 5am, when the paper was opened
    edited = start + 16 * 3600          # 8pm, when the drawing stopped
    assert journal_moment(edited, flagged) == edited
    assert journal_moment(edited, flagged) > flagged


def test_an_edit_on_a_later_day_clamps_to_the_filed_day_s_last_second():
    flagged = int(time.time()) - 3 * 86400
    start, end = _day(flagged)
    edited = flagged + 2 * 86400
    at = journal_moment(edited, flagged)
    assert at == end - 1
    # Still inside the day it was filed under, which is what the clamp is for.
    assert day_key_for(at) == day_key_for(flagged)


def test_an_edit_before_the_filed_day_clamps_up_to_its_start():
    flagged = int(time.time())
    start, _ = _day(flagged)
    at = journal_moment(flagged - 5 * 86400, flagged)
    assert at == start
    assert day_key_for(at) == day_key_for(flagged)


def test_no_recorded_edit_falls_back_to_the_flag_moment():
    flagged = int(time.time())
    assert journal_moment(None, flagged) == flagged


def test_a_paper_flagged_after_midnight_stays_on_the_day_it_was_lived():
    """The 4am rollover: 01:30 belongs to the day that started the morning
    before, and the clamp must use that day's window, not the calendar date's."""
    flagged = int(time.time())
    start, end = _day(flagged)
    just_before_rollover = end - 60
    at = journal_moment(just_before_rollover, flagged)
    assert at == just_before_rollover
    assert day_key_for(at) == day_key_for(flagged)
