"""When something was captured, as the capturing device said it.

An offline capture can reach the server hours after it was made, so a route
that files by "now" puts it on the wrong day. The journal and the food log both
take the device's own `capturedAt` for that reason; this is the one parser.
"""
from datetime import datetime


def parse_capture_time(value: str | None) -> datetime:
    """Parse an ISO timestamp carrying the capture device's UTC offset."""
    if not isinstance(value, str):
        raise ValueError('capturedAt must be an ISO timestamp')
    try:
        captured = datetime.fromisoformat((value or '').strip())
    except ValueError as exc:
        raise ValueError('capturedAt must be an ISO timestamp') from exc
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError('capturedAt must include a UTC offset')
    return captured


def optional_capture_time(values) -> int | None:
    """Offline capture keeps its original day, including on replay.

    Omission preserves the existing browser contract. An explicit malformed
    value must not silently file a capture under the day it was uploaded.
    """
    if 'capturedAt' not in values:
        return None
    try:
        return int(parse_capture_time(values['capturedAt']).timestamp())
    except (OverflowError, OSError) as exc:
        raise ValueError('capturedAt is outside the supported range') from exc
