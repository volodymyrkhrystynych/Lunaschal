"""The one switch that stops the jobs scheduler.

The whole point of this being a flag rather than a mass `UPDATE ... SET
enabled=0` is that a pause must be lossless — so most of what is pinned here is
what a pause *does not* touch.
"""
from datetime import datetime
from unittest.mock import patch

import pytest

from backend.jobs import scheduler


@pytest.fixture
def db(client):
    from backend.db.connection import get_db
    return get_db()


def _pause(db, paused=True):
    db.execute('UPDATE settings SET jobs_paused=?', (1 if paused else 0,))
    db.commit()


# --------------------------------------------------------------------------
# What the flag stops, and what it deliberately does not
# --------------------------------------------------------------------------

def test_unpaused_is_the_default(db):
    assert scheduler.is_paused(db) is False


def test_a_pause_stops_every_sweep_that_fetches_or_uses_the_model(db):
    _pause(db)
    with patch.object(scheduler.sync, 'run_sync_sweep') as sync_sweep, \
         patch.object(scheduler.career_watch, 'run_due') as careers, \
         patch.object(scheduler.workday_watch, 'run_due') as workday, \
         patch.object(scheduler.triager, 'run_gate_sweep') as gate, \
         patch.object(scheduler.triager, 'drain_once') as triage, \
         patch.object(scheduler.queue, 'drain_once') as resumes, \
         patch.object(scheduler.linker, 'run_linkage_sweep', return_value={}):
        results, _ = scheduler.tick(now=datetime(2026, 8, 30, 12, 0))

    for mock in (sync_sweep, careers, workday, gate, triage, resumes):
        mock.assert_not_called()
    assert results['paused'] is True


def test_local_bookkeeping_keeps_running_through_a_pause(db):
    """Linkage, ghosting and retention cost nothing and reach nobody.

    Stopping them would quietly rot the pipeline while the user believed they
    had paused only fetching — a rejection email that arrived during a
    fortnight's pause should still be on the application when it ends.
    """
    _pause(db)
    with patch.object(scheduler.linker, 'run_linkage_sweep',
                      return_value={'linked': 1}) as linkage, \
         patch.object(scheduler.outcomes, 'mark_ghosted_applications',
                      return_value={'ghosted': 0}) as ghosting, \
         patch.object(scheduler.retention, 'run_purge_sweep',
                      return_value={'purged': 0}) as purge, \
         patch.object(scheduler.sync, 'run_sync_sweep'):
        results, _ = scheduler.tick(now=datetime(2026, 8, 30, 7, 30))

    linkage.assert_called_once()
    ghosting.assert_called_once()
    purge.assert_called_once()
    assert results['linkage'] == {'linked': 1}


def test_resuming_runs_the_sweeps_again(db):
    _pause(db)
    _pause(db, paused=False)
    with patch.object(scheduler.sync, 'run_sync_sweep',
                      return_value={'added': 0}) as sync_sweep, \
         patch.object(scheduler.career_watch, 'run_due'), \
         patch.object(scheduler.workday_watch, 'run_due'), \
         patch.object(scheduler.triager, 'run_gate_sweep'), \
         patch.object(scheduler.triager, 'drain_once') as triage, \
         patch.object(scheduler.queue, 'drain_once'), \
         patch.object(scheduler.linker, 'run_linkage_sweep', return_value={}):
        results, _ = scheduler.tick(now=datetime(2026, 8, 30, 12, 0))

    sync_sweep.assert_called_once()
    triage.assert_called_once()
    assert results['paused'] is False


def test_an_unreadable_flag_does_not_take_the_tick_down(db):
    """A database that has not migrated yet must behave as it did before."""
    with patch.object(scheduler, 'is_paused', side_effect=RuntimeError('no column')), \
         patch.object(scheduler.sync, 'run_sync_sweep') as sync_sweep, \
         patch.object(scheduler.career_watch, 'run_due'), \
         patch.object(scheduler.workday_watch, 'run_due'), \
         patch.object(scheduler.triager, 'run_gate_sweep'), \
         patch.object(scheduler.triager, 'drain_once'), \
         patch.object(scheduler.queue, 'drain_once'), \
         patch.object(scheduler.linker, 'run_linkage_sweep', return_value={}):
        results, _ = scheduler.tick(now=datetime(2026, 8, 30, 12, 0))

    sync_sweep.assert_called_once()
    assert results['paused'] is False


# --------------------------------------------------------------------------
# Losslessness — the reason this is a flag at all
# --------------------------------------------------------------------------

def test_pausing_and_resuming_leaves_every_per_source_switch_alone(client):
    """A pause that rewrote `enabled` would forget what the user turned off."""
    from backend.db.connection import get_db

    on = client.post('/api/jobs/searches',
                     json={'kind': 'greenhouse', 'params': {'slug': 'ada18'}}).get_json()
    off = client.post('/api/jobs/searches',
                      json={'kind': 'lever', 'params': {'slug': 'achievers'},
                            'enabled': False}).get_json()
    assert bool(on['enabled']) and not bool(off['enabled'])

    client.post('/api/jobs/pause', json={'paused': True})
    client.post('/api/jobs/pause', json={'paused': False})

    db = get_db()
    rows = {r['id']: r['enabled'] for r in
            db.execute('SELECT id, enabled FROM job_searches').fetchall()}
    assert rows[on['id']] == 1
    assert rows[off['id']] == 0


# --------------------------------------------------------------------------
# The routes behind the button
# --------------------------------------------------------------------------

def test_get_reports_the_state_and_what_it_holds_back(client):
    client.post('/api/jobs/searches',
                json={'kind': 'greenhouse', 'params': {'slug': 'ada18'}})
    client.post('/api/jobs/searches',
                json={'kind': 'lever', 'params': {'slug': 'x'}, 'enabled': False})
    client.post('/api/jobs', json={'title': 'Engineer', 'company': 'Acme',
                                   'description': 'python'})

    state = client.get('/api/jobs/pause').get_json()
    assert state['paused'] is False
    assert state['sources'] == 1        # the disabled one is not held back
    assert state['pendingTriage'] == 1


def test_post_toggles_and_echoes_the_new_state(client):
    assert client.post('/api/jobs/pause', json={'paused': True}).get_json()['paused'] is True
    assert client.get('/api/jobs/pause').get_json()['paused'] is True
    assert client.post('/api/jobs/pause', json={'paused': False}).get_json()['paused'] is False


def test_post_without_a_value_is_rejected(client):
    """Defaulting a missing field here would pause the pipeline by accident."""
    response = client.post('/api/jobs/pause', json={})
    assert response.status_code == 400
