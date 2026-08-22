from pathlib import Path

import pytest

from backend.day_boundary import day_bounds, day_key_for
from backend.db.connection import get_db
from backend.notebook_diary_scheduler import promote_diary_notes


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setenv('NOTEBOOK_ROOT', str(tmp_path / 'notebook'))


def _write_diary(tmp_path: Path, date_key: str, content: str = '') -> None:
    diary_dir = tmp_path / 'notebook' / 'diary'
    diary_dir.mkdir(parents=True, exist_ok=True)
    (diary_dir / f'{date_key}.md').write_text(content, encoding='utf-8')


def _app_day_key(days_ago: int) -> str:
    """The date key of an app-day (4am-anchored, see day_boundary.py) that
    ended `days_ago` full app-days before today's."""
    today_start = day_bounds(day_key_for())[0]
    return day_key_for(today_start - 86400 * days_ago - 1)


def test_promotes_an_old_unpromoted_note(client, tmp_path):
    date_key = _app_day_key(1)
    _write_diary(tmp_path, date_key, 'Went for a walk.')

    assert promote_diary_notes() == 1

    row = get_db().execute(
        'SELECT content, raw_content, created_at FROM journal_entries'
    ).fetchone()
    assert row['content'] == 'Went for a walk.'
    assert row['raw_content'] == 'Went for a walk.'
    assert row['created_at'] == day_bounds(date_key)[0]

    promo = get_db().execute(
        'SELECT journal_entry_id FROM notebook_diary_promotions WHERE date=?',
        (date_key,),
    ).fetchone()
    entry_id = get_db().execute('SELECT id FROM journal_entries').fetchone()['id']
    assert promo is not None
    assert promo['journal_entry_id'] == entry_id


def test_skips_an_already_promoted_note(client, tmp_path):
    date_key = _app_day_key(1)
    _write_diary(tmp_path, date_key, 'First pass.')
    assert promote_diary_notes() == 1

    assert promote_diary_notes() == 0
    count = get_db().execute('SELECT COUNT(*) c FROM journal_entries').fetchone()['c']
    assert count == 1


def test_skips_todays_note(client, tmp_path):
    _write_diary(tmp_path, day_key_for(), 'Still being written.')
    assert promote_diary_notes() == 0
    assert get_db().execute('SELECT COUNT(*) c FROM journal_entries').fetchone()['c'] == 0


def test_skips_an_empty_note(client, tmp_path):
    date_key = _app_day_key(1)
    _write_diary(tmp_path, date_key, '   \n')
    assert promote_diary_notes() == 0
    assert get_db().execute('SELECT COUNT(*) c FROM journal_entries').fetchone()['c'] == 0


def test_catches_up_multiple_missed_days_in_one_run(client, tmp_path):
    keys = [_app_day_key(n) for n in (3, 2, 1)]
    for k in keys:
        _write_diary(tmp_path, k, f'Notes from {k}')

    assert promote_diary_notes() == 3
    assert get_db().execute('SELECT COUNT(*) c FROM journal_entries').fetchone()['c'] == 3
    promoted_dates = {
        r['date']
        for r in get_db().execute('SELECT date FROM notebook_diary_promotions').fetchall()
    }
    assert promoted_dates == set(keys)


def test_no_diary_folder_is_a_noop(client, tmp_path):
    assert promote_diary_notes() == 0
