"""Where a filed paper, study source or newspaper sits in its day."""
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


def test_an_unopened_newspaper_sorts_to_the_end_of_its_day():
    """The card is the paper still waiting, not a 6am event.

    A newspaper is stamped by the downloader, so unlike a flag its filing
    moment is nobody's gesture and burying the day's paper underneath the whole
    day is the one thing it must not do.
    """
    downloaded = int(time.time())
    start, end = _day(downloaded)
    at = journal_moment(None, downloaded, unworked_at_day_end=True)
    assert at == end - 1
    assert day_key_for(at) == day_key_for(downloaded)


def test_a_newspaper_that_was_read_sits_at_the_reading_not_the_day_end():
    """The day end is only the fallback — a real reading moment beats it."""
    start, end = _day(int(time.time()))
    downloaded = start + 2 * 3600      # 6am, when the downloader ran
    read = start + 15 * 3600           # 7pm, when it was actually read
    assert journal_moment(read, downloaded, unworked_at_day_end=True) == read
    assert read < end - 1


def test_reading_monday_s_paper_on_wednesday_keeps_it_in_monday():
    """Clamped like everything else: the card is view-only, and Monday's record
    would otherwise lose its paper to a day it cannot be read in context of."""
    downloaded = int(time.time()) - 2 * 86400
    _, end = _day(downloaded)
    at = journal_moment(int(time.time()), downloaded, unworked_at_day_end=True)
    assert at == end - 1
    assert day_key_for(at) == day_key_for(downloaded)


def test_the_day_end_fallback_is_off_unless_asked_for():
    """Papers and study sources keep the flag moment: somebody chose it."""
    flagged = int(time.time())
    assert journal_moment(None, flagged) == flagged
    assert journal_moment(None, flagged) != journal_moment(
        None, flagged, unworked_at_day_end=True)
