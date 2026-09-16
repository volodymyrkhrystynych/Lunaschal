"""The rules that turn a site's personal tags into library folders.

The invariant worth protecting here is that the sync can only ever undo its
own filing: whatever the user put in a folder by hand stays there, however
the labels on the site change.
"""

import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.fanfic.personal_tags import normalize, sync_personal_folders


@pytest.fixture
def fic_id():
    db = get_db()
    now, fic = int(time.time()), str(ULID())
    db.execute(
        'INSERT INTO fics(id,title,source_type,source_url,site,thread_id,created_at,updated_at)'
        ' VALUES (?,?,?,?,?,?,?,?)',
        (fic, 'A Fic', 'xenforo', 'https://forums.spacebattles.com/threads/a.1/',
         'forums.spacebattles.com', '1', now, now))
    db.commit()
    return fic


def folders_of(fic_id):
    return {(r['name'], r['origin']) for r in get_db().execute(
        'SELECT f.name, i.origin FROM fic_folder_items i'
        ' JOIN fic_folders f ON f.id = i.folder_id WHERE i.fic_id=?', (fic_id,))}


def make_folder(name, origin='manual'):
    db = get_db()
    now, folder_id = int(time.time()), str(ULID())
    db.execute('INSERT INTO fic_folders(id,name,position,origin,created_at,updated_at)'
               ' VALUES (?,?,0,?,?,?)', (folder_id, name, origin, now, now))
    db.commit()
    return folder_id


def test_normalize_trims_collapses_and_dedupes_case_insensitively():
    assert normalize(['  Slow   Burn ', 'slow burn', '', '   ', 'Worm']) == \
        ['Slow Burn', 'Worm']


def test_normalize_caps_a_runaway_name_and_skips_non_strings():
    assert normalize(['x' * 500, None, 3]) == ['x' * 100]


def test_tags_become_folders(fic_id):
    sync_personal_folders(get_db(), fic_id, ['Slow Burn', 'Worm'])
    assert folders_of(fic_id) == {('Slow Burn', 'import'), ('Worm', 'import')}


def test_an_existing_folder_is_reused_rather_than_twinned(fic_id):
    folder_id = make_folder('Worm')
    sync_personal_folders(get_db(), fic_id, ['worm'])
    rows = get_db().execute('SELECT id, name, origin FROM fic_folders').fetchall()
    assert [(r['id'], r['name'], r['origin']) for r in rows] == \
        [(folder_id, 'Worm', 'manual')]


def test_a_removed_label_unfiles_the_fic(fic_id):
    db = get_db()
    sync_personal_folders(db, fic_id, ['Slow Burn', 'Worm'])
    sync_personal_folders(db, fic_id, ['Worm'])
    assert folders_of(fic_id) == {('Worm', 'import')}
    # The folder itself survives — it may still hold other fics.
    assert db.execute("SELECT COUNT(*) c FROM fic_folders").fetchone()['c'] == 2


def test_a_hand_filed_fic_survives_the_label_disappearing(fic_id):
    db = get_db()
    folder_id = make_folder('Currently reading')
    db.execute('INSERT INTO fic_folder_items(folder_id,fic_id,origin,created_at)'
               " VALUES (?,?,'manual',?)", (folder_id, fic_id, int(time.time())))
    db.commit()
    sync_personal_folders(db, fic_id, ['Worm'])
    assert folders_of(fic_id) == {('Currently reading', 'manual'), ('Worm', 'import')}


def test_a_label_matching_a_hand_filed_membership_leaves_it_manual(fic_id):
    db = get_db()
    folder_id = make_folder('Worm')
    db.execute('INSERT INTO fic_folder_items(folder_id,fic_id,origin,created_at)'
               " VALUES (?,?,'manual',?)", (folder_id, fic_id, int(time.time())))
    db.commit()
    sync_personal_folders(db, fic_id, ['Worm'])
    sync_personal_folders(db, fic_id, [])
    sync_personal_folders(db, fic_id, ['Something Else'])
    # Still filed by hand where the user put it, though the label is long gone.
    assert ('Worm', 'manual') in folders_of(fic_id)


def test_no_tags_is_a_no_op_rather_than_a_clear_out(fic_id):
    db = get_db()
    sync_personal_folders(db, fic_id, ['Worm'])
    # An unlabelled bookmark and a parse that found nothing are the same
    # bytes from here; only one of them should empty a fic's folders.
    assert sync_personal_folders(db, fic_id, []) == []
    assert folders_of(fic_id) == {('Worm', 'import')}


def test_repeated_syncs_are_idempotent(fic_id):
    db = get_db()
    first = sync_personal_folders(db, fic_id, ['Worm', 'Slow Burn'])
    assert sync_personal_folders(db, fic_id, ['Worm', 'Slow Burn']) == first
    assert db.execute('SELECT COUNT(*) c FROM fic_folder_items').fetchone()['c'] == 2
