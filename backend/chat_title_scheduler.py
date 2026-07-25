"""Nightly job that titles each day's chat.

There is no general scheduler in the Flask backend; this is a small daemon
thread that fires once per day in a narrow overnight window (strict — if the
machine is asleep/off at 2am the day's chat keeps a date fallback label in the
journal). The sweep body is a plain function so tests can call it directly.
"""
import os
import threading
import time
from datetime import datetime

from backend.db.connection import get_db
from backend.ai.chat_title import generate_conversation_title

# Local-hour window the sweep is allowed to fire in: [start, end).
TITLE_WINDOW_START_HOUR = 2
TITLE_WINDOW_END_HOUR = 3
_POLL_SECONDS = 300


def run_title_sweep() -> int:
    """Title every dated chat that still lacks one and has real messages.
    Returns how many titles were written. Already-titled chats are left alone."""
    db = get_db()
    rows = db.execute(
        '''SELECT c.id FROM conversations c
           WHERE c.day_key IS NOT NULL AND c.title IS NULL AND c.writing_project_id IS NULL
             AND EXISTS (SELECT 1 FROM messages m
                         WHERE m.conversation_id = c.id AND m.role IN ('user', 'assistant'))'''
    ).fetchall()
    written = 0
    for r in rows:
        msgs = db.execute(
            'SELECT role, content, metadata FROM messages WHERE conversation_id=? ORDER BY created_at',
            (r['id'],),
        ).fetchall()
        title = generate_conversation_title([dict(m) for m in msgs])
        if title:
            db.execute('UPDATE conversations SET title=? WHERE id=?', (title, r['id']))
            db.commit()
            written += 1
    return written


def _scheduler_loop() -> None:
    last_run_date = None
    while True:
        try:
            now = datetime.now()
            in_window = TITLE_WINDOW_START_HOUR <= now.hour < TITLE_WINDOW_END_HOUR
            if in_window and last_run_date != now.date():
                last_run_date = now.date()
                run_title_sweep()
        except Exception as e:
            print(f'Chat title sweep failed: {e}')
        time.sleep(_POLL_SECONDS)


def start_title_scheduler() -> None:
    # Werkzeug debug reloader forks two processes; only start in the child.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and os.environ.get('FLASK_DEBUG'):
        return
    threading.Thread(target=_scheduler_loop, daemon=True).start()
