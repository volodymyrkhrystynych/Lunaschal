"""Where a thing flagged for the Journal sits in the day it was filed under.

Papers and Study sources both move into the Journal lazily: you flag one, and
it stays where it is until the next 4am boundary passes (backend/day_boundary,
and the `_cutoff_4am` helpers in the two route modules). The flag decides
*which* day the item is filed under. This module decides *where in that day*
it appears -- which is not the same question, and used to be answered with the
flag timestamp for want of anything better.
"""
from backend.day_boundary import day_bounds, day_key_for


def journal_moment(edited_at: int | None, requested_at: int) -> int:
    """The moment a flagged item should occupy in the Journal feed.

    The last time it was actually worked on, clamped into the 4am day it was
    filed under. Two reasons for each half:

    - The last edit, not the flag, because the flag is a filing gesture. The
      "To journal" toggle sits in the editor's toolbar, so it is naturally hit
      when you *open* a paper -- which sorted a day's worth of drawing to the
      bottom of that day's feed, under everything that came after it.
    - Clamped, because the Journal card is view-only. An item edited on a later
      day would otherwise leave the day it was filed under and land in one the
      user cannot see it in context of, with no way to put it back.

    `edited_at` is None for an item nothing has recorded an edit for, and then
    the flag time is the only honest answer.
    """
    if edited_at is None:
        return requested_at
    start, end = day_bounds(day_key_for(requested_at))
    # end is exclusive: the last second that still belongs to this day is end-1.
    return max(start, min(edited_at, end - 1))
