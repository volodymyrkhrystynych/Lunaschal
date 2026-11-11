"""Overnight-briefing daemon, and the three passes that feed it.

(The entry-digest pass in backend/journal/digest_job.py runs first, since the
life wiki and the briefing both read the descriptions it writes; the
server-oddities scan in backend/oddities_job.py runs between the life wiki and
the briefing. The notes below about ordering apply to both.)

Like the chat-title sweep (backend/chat_title_scheduler.py), there is no general
scheduler in the Flask backend — this is a small daemon thread that fires once
per local date inside an early-morning window. The window starts at or after the
4am chat-day rollover so `day_key_for()` already points at today's fresh chat and
the briefing lands there. The run bodies live in backend/briefing_job.py and
backend/lifewiki/job.py so tests can call them directly.

**Two jobs, in one thread, in order — deliberately not two daemons.** The
briefing reads the life wiki, so the wiki has to be current before it starts; two
independently scheduled loops would eventually have the briefing reading a wiki
mid-rewrite, which is the failure that shape produces. Sequencing them here makes
the ordering a fact rather than a hope, and costs no extra llama slot: the two
run one after the other inside a window nothing else uses.

**The wiki pass is bounded and the briefing runs regardless.** Half a wiki plus a
briefing beats a complete wiki and no briefing, so the pass carries a wall-clock
budget and its failure is logged rather than raised.
"""
import os
import threading
import time
from datetime import datetime

from backend.db.connection import get_db
from backend.briefing_job import run_briefing

# The window is [hour, hour + WINDOW_SPAN_HOURS); poll interval below.
WINDOW_SPAN_HOURS = 2
_POLL_SECONDS = 300


def _briefing_settings() -> tuple[bool, int]:
    """(enabled, hour) from settings, with defaults when unset."""
    try:
        row = get_db().execute(
            'SELECT briefing_enabled, briefing_hour FROM settings LIMIT 1'
        ).fetchone()
    except Exception:
        return True, 5
    if not row:
        return True, 5
    enabled = bool(row['briefing_enabled'] if row['briefing_enabled'] is not None else 1)
    hour = row['briefing_hour'] if row['briefing_hour'] is not None else 5
    return enabled, hour


def run_nightly() -> None:
    """Entry digests, the life-wiki pass, the oddities scan, then the briefing.
    Order is the point.

    A failing wiki pass must not cost the briefing: the briefing is the thing
    the user wakes up to, and a picture one night out of date is a far smaller
    loss than no plan for the day. So the pass is wrapped separately from the
    call it exists to improve.
    """
    from backend.ai import service

    # The largest single consumer of the card in the app, and until now the only
    # scheduler that asked nobody's permission for it. P2 means a chat message
    # takes the lane back mid-pass. A pause still on when the window opens is
    # ended by `_tick` before this runs (see `_end_pause`); one switched on
    # mid-pass is still honoured, leaving the rest of the night pending.
    with service.background():
        _run_nightly()


def _run_nightly() -> None:
    """The four passes, each inside its own thinking scope.

    This thread is the one place where nobody is waiting on the model, so it is
    where thinking is switched on, with the long overnight budget
    (backend/ai/llm.py). The passes themselves never ask for thinking, so the
    same code run on demand — Settings' "Run now", a wiki rebuild — answers at
    interactive speed. Each pass's own deadline is what bounds the night.
    """
    import time as _time

    from backend.ai.llm import overnight_thinking
    from backend.lifewiki.job import DEFAULT_BUDGET_SECONDS, run_life_wiki_pass

    # First, because the life wiki and the briefing both read what it writes:
    # one description per entry, built from its text and every attachment's
    # reading together. Wrapped like the others — without it the readers fall
    # back to the entry's title and body, which is what they always had.
    try:
        from backend.journal import digest_job
        with overnight_thinking():
            result = digest_job.run_entry_digest_pass(
                deadline=_time.monotonic() + digest_job.DEFAULT_BUDGET_SECONDS
            )
        if result['written'] or result['failed']:
            print(f'Entry digests: {result["written"]} written, '
                  f'{result["failed"]} failed, {result["unsettled"]} still settling'
                  + (' (hit its budget)' if result['timedOut'] else ''))
    except Exception as e:
        print(f'Entry digest pass failed, briefing continuing: {e}')

    try:
        with overnight_thinking():
            result = run_life_wiki_pass(
                deadline=_time.monotonic() + DEFAULT_BUDGET_SECONDS
            )
        if result.get('facts') or result.get('articles'):
            print(f'Life-wiki pass: {result["facts"]} facts, '
                  f'{result["articles"]} articles, '
                  f'{result["observationsFolded"]} notes filed'
                  + (' (hit its budget)' if result.get('timedOut') else ''))
    except Exception as e:
        print(f'Life-wiki pass failed, briefing continuing: {e}')

    # After the wiki, so the night's own passes are in the window it reads;
    # before the briefing, which ends with what it found. Wrapped for the same
    # reason as the wiki: the briefing is the thing the user wakes up to.
    try:
        from backend.oddities_job import run_oddities_pass
        with overnight_thinking():
            result = run_oddities_pass()
        if result['clusters']:
            print(f'Server oddities: {result["clusters"]} kinds from '
                  f'{result["events"]} events, {result["notes"]} notes written')
    except Exception as e:
        print(f'Server oddities pass failed, briefing continuing: {e}')

    with overnight_thinking():
        run_briefing()


# How long the phone sync log keeps superseded versions. A device that hasn't
# synced for longer must bootstrap again, which for downloaded library text is
# a Wi-Fi re-download, so this is generous.
SYNC_HISTORY_DAYS = 90


def compact_sync_history() -> None:
    """Trim the phone sync log after the morning's model work.

    No model call, so it needs no slot; it runs here only because this is the
    one thread that already wakes at the start of the day, and after the
    briefing so it never delays it. A failure costs nothing but disk.
    """
    from backend.mobile_sync.maintenance import compact
    try:
        removed = compact(keep_days=SYNC_HISTORY_DAYS)
        if removed:
            print(f'Sync log compacted: {removed} superseded versions removed')
    except Exception as e:
        print(f'Sync log compaction failed: {e}')


def _end_pause() -> None:
    """Switch the GPU back on if "pause for gaming" was left on overnight.

    A pause is measured in hours, and by the briefing window the evening it was
    for is over — a pause still on at 05:00 is one somebody forgot, and honouring
    it would cost the briefing, the entry digests and the life-wiki pass for a
    card nobody is using. Only called when the briefing is enabled: with nothing
    to run, there is no reason to take the card back.
    """
    from backend.ai import service

    if not service.is_paused():
        return
    from backend.ai.pause import resume_inference
    resume_inference('overnight briefing')
    print('Overnight briefing: inference was paused, resumed it')


def _tick(now: datetime, last_run_date):
    """One poll: the briefing (when enabled), then compaction, once a day in
    the briefing window. Returns the date it last ran for."""
    enabled, hour = _briefing_settings()
    in_window = hour <= now.hour < hour + WINDOW_SPAN_HOURS
    if not in_window or last_run_date == now.date():
        return last_run_date
    try:
        if enabled:
            _end_pause()
            run_nightly()
    except Exception as e:
        print(f'Overnight briefing failed: {e}')
    compact_sync_history()
    return now.date()


def _scheduler_loop() -> None:
    last_run_date = None
    while True:
        try:
            last_run_date = _tick(datetime.now(), last_run_date)
        except Exception as e:
            print(f'Overnight briefing failed: {e}')
        time.sleep(_POLL_SECONDS)


def start_briefing_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    threading.Thread(target=_scheduler_loop, daemon=True).start()
