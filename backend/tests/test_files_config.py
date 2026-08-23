"""backend/files_config.py: Settings as the source of truth for where the
Files tab's cloud-drive root lives, mirroring backup_config's shape.
"""

from backend.db.connection import get_db
from backend.files_config import get_config, set_config, validate_root


def test_unset_by_default():
    assert get_config(get_db()) == {'path': '', 'source': 'unset'}


def test_set_and_get_round_trip():
    set_config(get_db(), path='/media/expansion/lunaschal-files')
    assert get_config(get_db()) == {
        'path': '/media/expansion/lunaschal-files',
        'source': 'settings',
    }


def test_setting_an_empty_path_clears_it():
    set_config(get_db(), path='/media/expansion/lunaschal-files')
    set_config(get_db(), path='')
    assert get_config(get_db())['source'] == 'unset'


def test_validate_rejects_a_relative_path():
    assert 'absolute' in validate_root('media/files')


def test_validate_rejects_an_empty_path():
    assert validate_root('   ') is not None


def test_validate_rejects_a_file(tmp_path):
    f = tmp_path / 'not-a-dir'
    f.write_text('x')
    assert 'file' in validate_root(str(f))


def test_validate_accepts_a_path_that_does_not_exist_yet(tmp_path):
    # Unlike the backup destination, a not-yet-created folder is fine — the
    # blueprint mkdir(parents=True)s it on first use.
    assert validate_root(str(tmp_path / 'brand-new-folder')) is None


def test_migration_is_idempotent():
    from backend.db.connection import _ensure_files_settings

    db = get_db()
    _ensure_files_settings(db)
    _ensure_files_settings(db)
    cols = [r[1] for r in db.execute('PRAGMA table_info(settings)')]
    assert cols.count('files_root') == 1
