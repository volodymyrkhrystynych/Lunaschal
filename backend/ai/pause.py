"""Switching the GPU lane back on, from wherever that is decided.

Two callers: the Settings button (`POST /api/settings/inference/resume`) and the
overnight briefing, which ends a pause left on from the evening before rather
than skipping the night. One function so the two cannot disagree about what
"resumed" means — the flag, the cache, the log line and the queue wake-up.
"""
import time

from backend.ai import jobs, service
from backend.db.connection import get_db


def resume_inference(reason: str | None = None) -> None:
    """Clear the pause flag and wake the queue.

    Deliberately no `/models/load`: the next real request reloads the model
    lazily, and spending tens of seconds pulling 22 GB onto the card because
    somebody pressed a button is a surprise, not a service.
    """
    db = get_db()
    db.execute('UPDATE settings SET inference_paused=0, inference_paused_at=NULL,'
               ' updated_at=? WHERE id=1', (int(time.time()),))
    db.commit()
    service.invalidate_pause_cache()
    detail = f'{jobs.pending_count()} job(s) queued'
    service.note('resume', f'{detail} ({reason})' if reason else detail)
    jobs.wake()
