"""The app-wide day boundary: every "day" concept in Lunaschal runs 04:00 ->
04:00 local time, except newspapers (an edition's date comes from the source
site, not the user's day). A task completed, a workout logged, or a message
sent at 02:00 still belongs to the day that started the previous morning.
"""
import time
from datetime import datetime, timedelta

# Hour (local) at which the chat day rolls over.
DAY_ROLLOVER_HOUR = 4


def day_key_for(ts: int | None = None) -> str:
    """Return the YYYY-MM-DD key of the 4am-anchored local day for a unix ts."""
    when = datetime.fromtimestamp(ts if ts is not None else time.time())
    return (when - timedelta(hours=DAY_ROLLOVER_HOUR)).date().isoformat()


def day_bounds(day_key: str) -> tuple[int, int]:
    """Unix bounds of a day key's window: [key 04:00, key+1 04:00).

    The inverse of day_key_for, and the same rollover: every timestamp t with
    start <= t < end satisfies day_key_for(t) == day_key. Local time, naive
    datetimes -- the app has one user in one timezone, and the DST-shifted day
    that is 23 or 25 hours long is still the day they lived.
    """
    start = datetime.fromisoformat(day_key).replace(hour=DAY_ROLLOVER_HOUR)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())
