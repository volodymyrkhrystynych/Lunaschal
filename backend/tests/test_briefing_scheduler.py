"""The briefing window ends a "pause for gaming" left on overnight.

A paused GPU used to skip the whole night — briefing, entry digests, life wiki —
because everything in the thread runs under `service.background()` and P2 work
waits out a pause. A pause switched on before today's 4am boundary is one
somebody forgot; one switched on since then is honoured.
"""
from datetime import datetime

import pytest

from backend import briefing_scheduler
from backend.ai import service
from backend.db.connection import get_db

IN_WINDOW = datetime(2026, 11, 11, 5, 30)


@pytest.fixture(autouse=True)
def clean(client):
    service.reset()
    yield
    service.reset()


@pytest.fixture
def nightly(monkeypatch):
    """Record each run, and whether the GPU was paused when it started."""
    runs = []
    monkeypatch.setattr(briefing_scheduler, 'run_nightly',
                        lambda: runs.append(service.is_paused()))
    monkeypatch.setattr(briefing_scheduler, 'compact_sync_history', lambda: None)
    return runs


def _set(**cols):
    db = get_db()
    db.execute('INSERT OR IGNORE INTO settings(id, created_at, updated_at) VALUES (1,0,0)')
    for col, value in cols.items():
        db.execute(f'UPDATE settings SET {col}=? WHERE id=1', (value,))
    db.commit()
    service.invalidate_pause_cache()


def _pause(at: datetime | None = datetime(2026, 11, 10, 22, 0)):
    """Paused at `at` — by default the evening before IN_WINDOW."""
    _set(inference_paused=1,
         inference_paused_at=int(at.timestamp()) if at else None)


def _row():
    return get_db().execute(
        'SELECT inference_paused, inference_paused_at FROM settings WHERE id=1'
    ).fetchone()


def _resume_events():
    return [e for e in service.recent_events() if e['kind'] == 'resume']


def test_a_pause_left_on_is_ended_before_the_night_runs(nightly):
    _set(briefing_enabled=1, briefing_hour=5)
    _pause()

    assert briefing_scheduler._tick(IN_WINDOW, None) == IN_WINDOW.date()

    assert nightly == [False]  # ran, and the lane was already back on
    row = _row()
    assert row['inference_paused'] == 0
    assert row['inference_paused_at'] is None
    assert not service.is_paused()
    [event] = _resume_events()
    assert 'overnight briefing' in event['detail']


def test_a_disabled_briefing_leaves_the_pause_alone(nightly):
    _set(briefing_enabled=0, briefing_hour=5)
    _pause()

    briefing_scheduler._tick(IN_WINDOW, None)

    assert nightly == []
    assert _row()['inference_paused'] == 1
    assert _resume_events() == []


@pytest.mark.parametrize('now, last_run', [
    (datetime(2026, 11, 11, 23, 0), None),                      # gaming hours
    (datetime(2026, 11, 11, 3, 0), None),                       # before the window
    (IN_WINDOW, IN_WINDOW.date()),                              # already ran today
])
def test_outside_the_window_the_pause_holds(nightly, now, last_run):
    _set(briefing_enabled=1, briefing_hour=5)
    _pause()

    briefing_scheduler._tick(now, last_run)

    assert nightly == []
    assert _row()['inference_paused'] == 1
    assert _resume_events() == []


def test_an_unpaused_night_notes_no_resume(nightly):
    _set(briefing_enabled=1, briefing_hour=5, inference_paused=0)

    briefing_scheduler._tick(IN_WINDOW, None)

    assert nightly == [False]
    assert _resume_events() == []


def test_a_pause_with_no_timestamp_counts_as_forgotten(nightly):
    _set(briefing_enabled=1, briefing_hour=5)
    _pause(at=None)

    briefing_scheduler._tick(IN_WINDOW, None)

    assert nightly == [False]
    assert _row()['inference_paused'] == 0


@pytest.mark.parametrize('hour, paused_at, now', [
    # Switched on just before the window opened: today's decision, not last night's.
    (5, datetime(2026, 11, 11, 4, 50), datetime(2026, 11, 11, 5, 0)),
    # Switched on after the night ran, then a restart lost `last_run_date`.
    (5, datetime(2026, 11, 11, 5, 40), datetime(2026, 11, 11, 6, 0)),
    # An evening briefing hour: the pause is for tonight, not a leftover.
    (21, datetime(2026, 11, 11, 20, 30), datetime(2026, 11, 11, 21, 0)),
])
def test_a_pause_switched_on_since_4am_is_honoured(nightly, hour, paused_at, now):
    _set(briefing_enabled=1, briefing_hour=hour)
    _pause(at=paused_at)

    assert briefing_scheduler._tick(now, None) == now.date()

    assert nightly == [True]  # ran into the pause, leaving its work pending
    assert _row()['inference_paused'] == 1
    assert _resume_events() == []


def test_an_evening_briefing_ends_yesterdays_pause(nightly):
    _set(briefing_enabled=1, briefing_hour=21)
    _pause(at=datetime(2026, 11, 10, 20, 0))

    briefing_scheduler._tick(datetime(2026, 11, 11, 21, 0), None)

    assert nightly == [False]
    assert _row()['inference_paused'] == 0
