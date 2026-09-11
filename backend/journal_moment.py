"""Where a thing filed for the Journal sits in the day it was filed under.

Papers, Study sources and archived newspaper issues all get a card in the
Journal feed, and all three need the same two answers: *which* day, and *where
in that day*. The first is settled before this module is called -- a paper and
a study source carry a flag the user set (and move on the next 4am boundary,
see backend/day_boundary and the `_cutoff_4am` helpers in the route modules),
an issue carries the moment it was downloaded. This module answers only the
second, which is not the same question, and used to be answered with the
filing timestamp for want of anything better.
"""
from backend.day_boundary import day_bounds, day_key_for


def journal_moment(edited_at: int | None, filed_at: int, *,
                   unworked_at_day_end: bool = False) -> int:
    """The moment a filed item should occupy in the Journal feed.

    The last time it was actually worked on, clamped into the 4am day it was
    filed under. Two reasons for each half:

    - The last edit, not the filing moment, because filing is a gesture made on
      the way in. The "To journal" toggle sits in the editor's toolbar, so it
      is naturally hit when you *open* a paper -- which sorted a day's worth of
      drawing to the bottom of that day's feed, under everything that came
      after it. A newspaper has no gesture at all: it is stamped when the
      downloader ran, which is nobody's idea of when the paper was read.
    - Clamped, because the Journal card is view-only. An item worked on during
      a later day would otherwise leave the day it was filed under and land in
      one the user cannot see it in context of, with no way to put it back. So
      marking up Monday's paper on Wednesday moves the card within Monday, and
      Monday keeps its paper.

    `edited_at` is None for an item nothing has recorded work on, and what to
    do then depends on whether the filing moment means anything:

    - For a paper or a study source it does -- somebody chose that instant --
      so it is the only honest answer, and the default.
    - For a newspaper it does not: an issue nobody opened was stamped by the
      overnight downloader, and sorting it into the small hours buries the
      day's paper under the whole day. `unworked_at_day_end` puts it at the
      last second of its day instead, above that day's last entry, where an
      unread paper is a thing still waiting rather than a thing that happened
      at 6am.
    """
    start, end = day_bounds(day_key_for(filed_at))
    if edited_at is None:
        # end is exclusive: the last second still belonging to this day is end-1.
        return end - 1 if unworked_at_day_end else filed_at
    return max(start, min(edited_at, end - 1))
