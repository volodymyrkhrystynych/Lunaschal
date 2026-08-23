"""backend/routes/backup.py: the Settings → Backup status endpoint.

These point the route's module-level paths at a fake drive under tmp_path, so
the whole matrix — missing mount, read-only mount, stale snapshots, healthy —
is exercised without a 7TB disk attached. `_timer_info` is stubbed out in most
of them because there is no user systemd bus under pytest.
"""

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from backend.routes import backup as backup_routes


@pytest.fixture
def fake_drive(tmp_path, monkeypatch):
    """A destination directory laid out the way ops/backup.sh writes one.

    Returns a helper that writes ops/backup.env and dated db/ snapshots, so each
    test states only the situation it cares about.
    """
    repo = tmp_path / 'repo'
    (repo / 'ops').mkdir(parents=True)
    script = repo / 'ops' / 'backup.sh'
    script.write_text('#!/bin/sh\necho ran\n')
    script.chmod(0o755)

    monkeypatch.setattr(backup_routes, '_REPO_ROOT', repo)
    monkeypatch.setattr(backup_routes, '_ENV_PATH', repo / 'ops' / 'backup.env')
    monkeypatch.setattr(backup_routes, '_SCRIPT_PATH', script)
    from backend.ops import backup_config
    monkeypatch.setattr(backup_config, '_ENV_PATH', repo / 'ops' / 'backup.env')
    monkeypatch.setattr(backup_routes, '_timer_info', lambda: {
        'available': False, 'enabled': None, 'lastRun': None,
        'lastResult': None, 'nextRun': None,
    })
    monkeypatch.setattr(backup_routes, '_run_state', {
        'running': False, 'startedAt': None, 'finishedAt': None,
        'ok': None, 'output': None,
    })

    # The migration seeds backup_path from the real ops/backup.env, and the
    # session-wide schema template is built before this fixture can redirect
    # that path — so every test would otherwise start already configured with
    # whatever destination this developer's machine happens to use. Reset to
    # unconfigured so each test states its own situation.
    from backend.db.connection import get_db
    get_db().execute('UPDATE settings SET backup_path=NULL, backup_retention_days=14')
    get_db().commit()

    mount = tmp_path / 'mount'

    class Drive:
        dest = mount / 'lunaschal'
        mount_point = mount

        def write_env(self, path=None, retention=14):
            """Configure via the settings table — the source of truth now.

            Named for what it replaced so the tests below still read as "set the
            backup config"; the env file is only a fallback for a DB that has
            never had a path set (see configure_via_env_file_only).
            """
            from backend.db.connection import get_db
            target = self.dest if path is None else path
            get_db().execute(
                'UPDATE settings SET backup_path=?, backup_retention_days=?',
                (str(target), retention),
            )
            get_db().commit()

        def configure_via_env_file_only(self, path=None, retention=14):
            """The pre-migration arrangement: nothing in the DB, a path in the file."""
            from backend.db.connection import get_db
            get_db().execute('UPDATE settings SET backup_path=NULL')
            get_db().commit()
            target = self.dest if path is None else path
            (repo / 'ops' / 'backup.env').write_text(
                f'BACKUP_HDD_PATH={target}\n'
                f'BACKUP_RETENTION_DAYS={retention}\n'
            )

        def mount_drive(self):
            (self.dest / 'media').mkdir(parents=True, exist_ok=True)
            (self.dest / 'db').mkdir(parents=True, exist_ok=True)

        def add_snapshot(self, day: date, size: int = 1024):
            d = self.dest / 'db' / day.strftime('%Y-%m-%d')
            d.mkdir(parents=True, exist_ok=True)
            (d / 'lunaschal.db').write_bytes(b'x' * size)

    return Drive()


def test_status_reports_unreachable_when_the_configured_path_is_gone(client, fake_drive):
    """The nineteen-day failure, reproduced.

    Snapshots exist on a drive that is no longer where backup.env says it is.
    The nightly job skips and exits 0; the panel must say so out loud.
    """
    fake_drive.mount_drive()
    fake_drive.add_snapshot(date.today() - timedelta(days=19))
    fake_drive.write_env(path=Path('/nonexistent/elsewhere/lunaschal'))

    data = client.get('/api/backup/status').get_json()

    assert data['configured'] is True
    assert data['health'] == 'unreachable'
    assert data['mount']['present'] is False
    assert 'not present' in data['problems'][0]


def test_status_reports_stale_when_the_drive_is_reachable_but_backups_are_old(client, fake_drive):
    fake_drive.mount_drive()
    fake_drive.add_snapshot(date.today() - timedelta(days=19))
    fake_drive.write_env()

    data = client.get('/api/backup/status').get_json()

    assert data['health'] == 'stale'
    assert data['snapshots']['ageDays'] == 19
    assert data['snapshots']['count'] == 1


def test_status_is_ok_for_a_fresh_backup(client, fake_drive):
    fake_drive.mount_drive()
    for offset in range(3):
        fake_drive.add_snapshot(date.today() - timedelta(days=offset))
    fake_drive.write_env()

    data = client.get('/api/backup/status').get_json()

    assert data['health'] == 'ok'
    assert data['problems'] == []
    assert data['snapshots']['ageDays'] == 0
    assert data['snapshots']['count'] == 3
    # Three days of history under a 14-day window is complete, not missing 11.
    assert data['snapshots']['expectedCount'] == 3
    assert data['snapshots']['latestSizeBytes'] == 1024


def test_status_reports_permissions_for_an_unwritable_destination(client, fake_drive):
    """A destination on a read-write filesystem that this user cannot write to.

    This is what the real drive was doing: mounted rw, but every file owned by
    root because the fstab entry carried no uid=. tmp_path is on a read-write
    filesystem too, so chmod reproduces it exactly.
    """
    fake_drive.mount_drive()
    fake_drive.add_snapshot(date.today())
    fake_drive.write_env()
    os.chmod(fake_drive.dest, 0o555)
    try:
        data = client.get('/api/backup/status').get_json()
    finally:
        os.chmod(fake_drive.dest, 0o755)

    assert data['health'] == 'permissions'
    assert data['mount']['writable'] is False
    # The filesystem itself is fine — saying otherwise sends the user to fsck.
    assert data['mount']['readonly'] is False


def test_status_reports_readonly_when_the_mount_really_is_read_only(client, fake_drive, monkeypatch):
    fake_drive.mount_drive()
    fake_drive.add_snapshot(date.today())
    fake_drive.write_env()
    monkeypatch.setattr(backup_routes, '_is_writable', lambda p: False)
    monkeypatch.setattr(backup_routes, '_is_readonly_mount', lambda p: True)

    data = client.get('/api/backup/status').get_json()

    assert data['health'] == 'readonly'
    assert data['mount']['readonly'] is True


def test_status_reports_unconfigured_when_no_destination_is_set(client, fake_drive):
    data = client.get('/api/backup/status').get_json()

    assert data['configured'] is False
    assert data['health'] == 'unconfigured'
    assert data['configSource'] == 'unset'


def test_status_falls_back_to_the_env_file_when_the_db_has_no_path(client, fake_drive):
    """An install that predates the settings column keeps working.

    The migration seeds the DB from the file, but a DB that somehow still has no
    path must not report a configured backup as unconfigured and stop backing up.
    """
    fake_drive.mount_drive()
    fake_drive.add_snapshot(date.today())
    fake_drive.configure_via_env_file_only()

    data = client.get('/api/backup/status').get_json()

    assert data['configured'] is True
    assert data['configSource'] == 'backup.env'
    assert data['health'] == 'ok'


def test_a_not_yet_created_destination_counts_as_present_on_a_real_mount(
    client, fake_drive, monkeypatch
):
    """A hand-configured path whose folder the first run has yet to create.

    Only counts as present because the parent is a genuine mount point — see
    the next test for why that qualifier is doing real work.
    """
    fake_drive.mount_point.mkdir(parents=True, exist_ok=True)
    fake_drive.write_env()
    assert not fake_drive.dest.exists()
    monkeypatch.setattr(backup_routes, '_is_mount', lambda p: p == fake_drive.mount_point)

    data = client.get('/api/backup/status').get_json()

    assert data['mount']['present'] is True
    assert data['health'] == 'empty'


def test_a_missing_destination_under_an_ordinary_directory_is_unreachable(
    client, fake_drive
):
    """The safety property that stops a backup landing on the system disk.

    The parent exists but is not a mount point, so this is a path that points
    nowhere real — creating it would quietly mirror 6GB onto the internal drive
    while reporting success.
    """
    fake_drive.mount_point.mkdir(parents=True, exist_ok=True)
    fake_drive.write_env()
    assert not fake_drive.dest.exists()

    data = client.get('/api/backup/status').get_json()

    assert data['mount']['present'] is False
    assert data['health'] == 'unreachable'


def test_status_ignores_non_date_directories_under_db(client, fake_drive):
    fake_drive.mount_drive()
    fake_drive.add_snapshot(date.today())
    (fake_drive.dest / 'db' / 'lost+found').mkdir()

    fake_drive.write_env()
    data = client.get('/api/backup/status').get_json()

    assert data['snapshots']['count'] == 1
    assert data['snapshots']['dates'] == [date.today().strftime('%Y-%m-%d')]


def test_status_reports_retention_from_settings(client, fake_drive):
    fake_drive.mount_drive()
    fake_drive.write_env(retention=30)

    assert client.get('/api/backup/status').get_json()['retentionDays'] == 30


def test_status_falls_back_to_default_retention_on_a_bad_value(client, fake_drive):
    from backend.db.connection import get_db
    fake_drive.mount_drive()
    fake_drive.write_env()
    get_db().execute("UPDATE settings SET backup_retention_days=NULL")
    get_db().commit()

    assert client.get('/api/backup/status').get_json()['retentionDays'] == 14


def test_run_now_starts_the_script_and_reports_its_output(client, fake_drive):
    fake_drive.mount_drive()
    fake_drive.write_env()

    resp = client.post('/api/backup/run')
    assert resp.status_code == 200
    assert resp.get_json()['running'] is True

    # The worker thread is a real subprocess; poll the status endpoint the way
    # the UI does rather than reaching into the thread.
    for _ in range(100):
        data = client.get('/api/backup/status').get_json()['run']
        if not data['running']:
            break
        import time
        time.sleep(0.05)

    assert data['running'] is False
    assert data['ok'] is True
    assert 'ran' in data['output']


def test_run_now_refuses_a_second_concurrent_run(client, fake_drive):
    fake_drive.mount_drive()
    fake_drive.write_env()
    backup_routes._run_state['running'] = True

    resp = client.post('/api/backup/run')

    assert resp.status_code == 409
    assert 'already running' in resp.get_json()['error']


def test_run_now_reports_a_failing_script_rather_than_raising(client, fake_drive):
    backup_routes._SCRIPT_PATH.write_text('#!/bin/sh\necho "drive unplugged" >&2\nexit 1\n')
    backup_routes._SCRIPT_PATH.chmod(0o755)
    fake_drive.mount_drive()
    fake_drive.write_env()

    client.post('/api/backup/run')
    for _ in range(100):
        data = client.get('/api/backup/status').get_json()['run']
        if not data['running']:
            break
        import time
        time.sleep(0.05)

    assert data['ok'] is False
    assert 'drive unplugged' in data['output']


# --- Settings as the source of truth: the config and folder-picker endpoints ---


def test_put_config_sets_the_destination_the_job_will_use(client, fake_drive):
    """The round trip that makes Settings authoritative: what is saved here is
    what /status reports, with no env file involved."""
    fake_drive.mount_drive()

    resp = client.put('/api/backup/config',
                      json={'destination': str(fake_drive.dest)})

    assert resp.status_code == 200
    assert resp.get_json()['path'] == str(fake_drive.dest)
    status = client.get('/api/backup/status').get_json()
    assert status['destination'] == str(fake_drive.dest)
    assert status['configSource'] == 'settings'


def test_put_config_accepts_a_destination_that_is_not_plugged_in(client, fake_drive):
    # Configuring an absent drive is the normal case when fixing a broken
    # backup; rejecting it would make the panel useless exactly then.
    resp = client.put('/api/backup/config',
                      json={'destination': '/media/some-drive/lunaschal'})

    assert resp.status_code == 200
    assert client.get('/api/backup/status').get_json()['health'] == 'unreachable'


def test_put_config_rejects_a_relative_path(client, fake_drive):
    resp = client.put('/api/backup/config', json={'destination': 'relative/path'})

    assert resp.status_code == 400
    assert 'absolute' in resp.get_json()['error']


def test_put_config_rejects_a_file(client, fake_drive, tmp_path):
    f = tmp_path / 'a-file'
    f.write_text('x')

    resp = client.put('/api/backup/config', json={'destination': str(f)})

    assert resp.status_code == 400


def test_put_config_rejects_out_of_range_retention(client, fake_drive):
    assert client.put('/api/backup/config', json={'retentionDays': 0}).status_code == 400
    assert client.put('/api/backup/config', json={'retentionDays': 9999}).status_code == 400
    assert client.put('/api/backup/config',
                      json={'retentionDays': 'lots'}).status_code == 400


def test_put_config_updates_retention_alone(client, fake_drive):
    fake_drive.mount_drive()
    fake_drive.write_env()

    resp = client.put('/api/backup/config', json={'retentionDays': 30})

    assert resp.status_code == 200
    assert resp.get_json()['retentionDays'] == 30
    # Changing one field must not clear the other.
    assert resp.get_json()['path'] == str(fake_drive.dest)


def test_browse_lists_only_directories(client, fake_drive, tmp_path):
    root = tmp_path / 'browse'
    (root / 'a-folder').mkdir(parents=True)
    (root / 'b-folder').mkdir()
    (root / 'a-file.txt').write_text('x')
    (root / '.hidden').mkdir()

    data = client.get(f'/api/backup/browse?path={root}').get_json()

    assert [e['name'] for e in data['entries']] == ['a-folder', 'b-folder']
    assert data['parent'] == str(tmp_path)


def test_browse_reports_writability_per_entry(client, fake_drive, tmp_path):
    """What lets the picker warn before you choose an unwritable folder —
    the condition the real drive was in."""
    root = tmp_path / 'browse'
    locked = root / 'locked'
    locked.mkdir(parents=True)
    (root / 'open').mkdir()
    os.chmod(locked, 0o555)
    try:
        data = client.get(f'/api/backup/browse?path={root}').get_json()
    finally:
        os.chmod(locked, 0o755)

    by_name = {e['name']: e for e in data['entries']}
    assert by_name['open']['writable'] is True
    assert by_name['locked']['writable'] is False


def test_browse_root_has_no_parent(client, fake_drive):
    assert client.get('/api/backup/browse?path=/').get_json()['parent'] is None


def test_browse_offers_somewhere_to_start(client, fake_drive):
    # Walking down from / to find a USB drive every time would be miserable.
    data = client.get('/api/backup/browse?path=/').get_json()
    assert data['suggestions']
    assert all(Path(s['path']).is_dir() for s in data['suggestions'])


def test_browse_404s_on_a_missing_directory(client, fake_drive):
    assert client.get('/api/backup/browse?path=/no/such/dir').status_code == 404


def test_browse_survives_an_unreadable_directory(client, fake_drive, tmp_path):
    blocked = tmp_path / 'blocked'
    blocked.mkdir()
    os.chmod(blocked, 0o000)
    try:
        resp = client.get(f'/api/backup/browse?path={blocked}')
    finally:
        os.chmod(blocked, 0o755)

    assert resp.status_code == 403
    assert 'Permission denied' in resp.get_json()['error']
