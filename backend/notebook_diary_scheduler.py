"""Promotes finished Notebook diary notes (diary/YYYY-MM-DD.md, written via
the vim editor's <Space>w<Space>w jump — see NotebookEditorPane.tsx) into
Journal entries once their day is over.

There is no general scheduler in the Flask backend; this is a small daemon
thread that fires once per (wall-clock) day in a narrow overnight window, like
backend/chat_title_scheduler.py. It runs right after the app's 4am day
rollover (backend/day_boundary.py) so "yesterday" has just become promotable,
and it catches up on *every* unpromoted day it finds, not just the most
recent one — the app has no cron, so if the machine was asleep or off for
several nights, entries would otherwise be silently lost rather than merely
late. notebook_diary_promotions tracks what's already been done so a catch-up
run is idempotent. Unlike the other schedulers this makes no AI calls at all,
so it doesn't compete for a llama slot and its window placement is only about
running after the rollover, not about staggering against the others.
"""
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from backend.db.connection import get_db
from backend.day_boundary import day_key_for, day_bounds
from backend.routes.notebook import NOTEBOOK_ROOT_ENV, NOTEBOOK_DEFAULT_ROOT
from backend.routes.journal import create_journal_entry

WINDOW_START_HOUR = 4
WINDOW_END_HOUR = 5
_POLL_SECONDS = 300

_DIARY_FILE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\.md$')


def _notebook_root() -> Path:
    return Path(os.environ.get(NOTEBOOK_ROOT_ENV, NOTEBOOK_DEFAULT_ROOT)).expanduser().resolve()


def promote_diary_notes() -> int:
    """Promotes every not-yet-promoted diary note whose day has ended into a
    journal entry. Returns how many were promoted."""
    diary_dir = _notebook_root() / 'diary'
    if not diary_dir.is_dir():
        return 0

    today_key = day_key_for()
    db = get_db()
    promoted_keys = {
        r['date']
        for r in db.execute('SELECT date FROM notebook_diary_promotions').fetchall()
    }

    promoted = 0
    for entry in sorted(diary_dir.iterdir()):
        match = _DIARY_FILE_RE.match(entry.name)
        if not match or not entry.is_file():
            continue
        date_key = match.group(1)
        if date_key >= today_key or date_key in promoted_keys:
            continue
        try:
            text = entry.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        if not text.strip():
            continue

        created_at = day_bounds(date_key)[0]
        entry_id = create_journal_entry(text, text, created_at)
        if entry_id is None:
            continue
        db.execute(
            'INSERT OR IGNORE INTO notebook_diary_promotions(date, journal_entry_id, promoted_at)'
            ' VALUES (?,?,?)',
            (date_key, entry_id, int(time.time())),
        )
        db.commit()
        promoted += 1
    return promoted


def _scheduler_loop() -> None:
    last_run_date = None
    while True:
        try:
            now = datetime.now()
            in_window = WINDOW_START_HOUR <= now.hour < WINDOW_END_HOUR
            if in_window and last_run_date != now.date():
                last_run_date = now.date()
                promote_diary_notes()
        except Exception as e:
            print(f'Notebook diary promotion failed: {e}')
        time.sleep(_POLL_SECONDS)


def start_notebook_diary_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    threading.Thread(target=_scheduler_loop, daemon=True).start()
