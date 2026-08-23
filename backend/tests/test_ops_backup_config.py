"""backend/ops/backup_config.py: Settings as the source of truth for where the
nightly backup writes.

The property under test throughout is that the panel and `ops/backup.sh` read
the same value from the same place. They read different places once before, and
the drift cost nineteen days of backups.
"""

import os
from pathlib import Path

import pytest

from backend.db.connection import get_db
from backend.ops.backup_config import (
    DEFAULT_RETENTION_DAYS,
    clamp_retention,
    destination_present,
    get_config,
    nearest_mount_point,
    seed_from_env_file,
    set_config,
    validate_destination,
)


@pytest.fixture(autouse=True)
def clean_config():
    get_db().execute('UPDATE settings SET backup_path=NULL, backup_retention_days=14')
    get_db().commit()


def test_settings_wins_over_the_env_file(tmp_path):
    """The whole point: the file no longer decides anything once the DB has a
    path, so a stale BACKUP_HDD_PATH cannot silently redirect the backup."""
    env = tmp_path / 'backup.env'
    env.write_text('BACKUP_HDD_PATH=/old/stale/path\n')
    set_config(get_db(), path='/media/expansion/lunaschal')

    cfg = get_config(get_db())

    assert cfg['path'] == '/media/expansion/lunaschal'
    assert cfg['source'] == 'settings'


def test_env_file_is_used_only_when_settings_has_no_path(tmp_path, monkeypatch):
    from backend.ops import backup_config

    env = tmp_path / 'backup.env'
    env.write_text('BACKUP_HDD_PATH=/media/legacy/lunaschal\n')
    monkeypatch.setattr(backup_config, '_ENV_PATH', env)

    cfg = get_config(get_db())

    assert cfg['path'] == '/media/legacy/lunaschal'
    assert cfg['source'] == 'backup.env'


def test_config_is_unset_when_neither_source_has_a_path(tmp_path, monkeypatch):
    from backend.ops import backup_config

    monkeypatch.setattr(backup_config, '_ENV_PATH', tmp_path / 'nonexistent.env')

    assert get_config(get_db())['source'] == 'unset'


def test_seed_copies_the_env_path_into_an_empty_settings_row(tmp_path):
    """What keeps the upgrade from silently unconfiguring a working backup."""
    env = tmp_path / 'backup.env'
    env.write_text('BACKUP_HDD_PATH=/media/expansion/lunaschal\n')

    seeded = seed_from_env_file(get_db(), env)

    assert seeded == '/media/expansion/lunaschal'
    assert get_config(get_db())['path'] == '/media/expansion/lunaschal'


def test_seed_never_overwrites_a_path_the_user_already_set(tmp_path):
    env = tmp_path / 'backup.env'
    env.write_text('BACKUP_HDD_PATH=/media/from-the-file\n')
    set_config(get_db(), path='/media/chosen-in-settings')

    assert seed_from_env_file(get_db(), env) == ''
    assert get_config(get_db())['path'] == '/media/chosen-in-settings'


def test_setting_an_empty_path_clears_the_destination(tmp_path, monkeypatch):
    from backend.ops import backup_config

    monkeypatch.setattr(backup_config, '_ENV_PATH', tmp_path / 'nonexistent.env')
    set_config(get_db(), path='/media/expansion/lunaschal')

    set_config(get_db(), path='')

    assert get_config(get_db())['source'] == 'unset'


def test_retention_round_trips(tmp_path):
    set_config(get_db(), path='/media/x', retention_days=30)
    assert get_config(get_db())['retentionDays'] == 30


def test_clamp_retention_keeps_the_backup_runnable():
    # Read on the backup's own code path, so junk must degrade rather than raise
    # — a bad number in one column is no reason to skip the night entirely.
    assert clamp_retention(None) == DEFAULT_RETENTION_DAYS
    assert clamp_retention('nonsense') == DEFAULT_RETENTION_DAYS
    assert clamp_retention(0) == 1
    assert clamp_retention(-5) == 1
    assert clamp_retention(10_000) == 365
    assert clamp_retention('30') == 30


def test_validate_rejects_a_relative_path():
    assert 'absolute' in validate_destination('media/expansion')


def test_validate_rejects_an_empty_path():
    assert validate_destination('   ') is not None


def test_validate_rejects_a_file(tmp_path):
    f = tmp_path / 'not-a-dir'
    f.write_text('x')
    assert 'file' in validate_destination(str(f))


def test_validate_accepts_a_path_whose_drive_is_currently_unplugged():
    """Configuring an absent drive has to work — it is the case you are most
    likely to be in when you open this panel to fix something."""
    assert validate_destination('/media/expansion/lunaschal') is None


def test_validate_refuses_to_back_data_up_into_itself(tmp_path, monkeypatch):
    # Otherwise each run mirrors the previous run's mirror, forever.
    monkeypatch.setenv('LUNASCHAL_DATA_ROOT', str(tmp_path))
    inside = tmp_path / 'backups'
    inside.mkdir()

    assert 'inside the data directory' in validate_destination(str(inside))


def test_destination_present_when_the_folder_exists():
    assert destination_present(
        dest_exists=True, parent_exists=True, parent_is_mount=True) is True
    # The picker only offers existing folders, so this holds even off a mount.
    assert destination_present(
        dest_exists=True, parent_exists=True, parent_is_mount=False) is True


def test_destination_present_for_a_first_run_under_a_real_mount():
    assert destination_present(
        dest_exists=False, parent_exists=True, parent_is_mount=True) is True


def test_destination_absent_when_the_parent_is_not_a_mount():
    """The property that stops 6GB mirroring onto the system disk.

    A path whose parent is an ordinary directory is a path that points nowhere
    real; treating it as present would have backup.sh create it and report
    success.
    """
    assert destination_present(
        dest_exists=False, parent_exists=True, parent_is_mount=False) is False


def test_destination_absent_when_nothing_exists():
    assert destination_present(
        dest_exists=False, parent_exists=False, parent_is_mount=False) is False


def test_nearest_mount_point_walks_up_to_a_real_mount(tmp_path):
    """Whatever it returns must be an actual mount point and an ancestor.

    Asserted as a property rather than a literal: whether /tmp is its own tmpfs
    varies by machine, so pinning the expected string would encode this
    developer's setup rather than the behaviour.
    """
    deep = tmp_path / 'a' / 'b'
    result = nearest_mount_point(str(deep))

    assert result is not None
    assert os.path.ismount(result)
    assert str(deep).startswith(result)


def test_nearest_mount_point_of_the_root_is_the_root():
    assert nearest_mount_point('/') == '/'


def test_nearest_mount_point_is_none_when_the_path_is_bogus():
    # Not an error case for the caller: "no mount point" is exactly how the
    # status panel learns the drive is not there.
    assert nearest_mount_point('\x00not-a-path') is None
