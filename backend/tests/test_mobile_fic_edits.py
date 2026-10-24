"""Library edits from the phone: rating, review, shelf, folders, read marks."""
import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.tests.test_mobile_books import book


def epoch(client):
    return client.get('/api/mobile/sync?collections=fics').json['epoch']


def fic_edit(client, fic, data, revision=1):
    return dict(id=str(ULID()), epoch=epoch(client), collection='fics', recordId=fic,
                baseRevision=revision, action='update', data=data)


def read_edit(client, fic, chapter, action='create'):
    return dict(id=str(ULID()), epoch=epoch(client), collection='fic_chapter_reads',
                recordId=chapter, baseRevision=0, action=action, data={'ficId': fic})


def send(client, body):
    return client.post('/api/mobile/operations', json=body)


def folder(name):
    db = get_db()
    folder_id = str(ULID())
    db.execute('INSERT INTO fic_folders(id,name,created_at,updated_at) VALUES (?,?,1,1)', (folder_id, name))
    db.commit()
    return folder_id


def test_drop_with_a_review_reaches_the_desktop_and_the_replica(client):
    fic, _ = book()
    body = fic_edit(client, fic, {'rating': 2, 'review': '  Lost me at the council arc. ', 'shelf': 'dropped'})
    response = send(client, body)
    assert response.status_code == 200
    data = response.json['change']['data']
    assert (data['rating'], data['review'], data['shelf']) == (2, 'Lost me at the council arc.', 'dropped')
    assert data['shelvedAt'] is not None
    assert send(client, body).json == response.json  # a replay is the same receipt
    listed = next(f for f in client.get('/api/fanfic').json if f['id'] == fic)
    assert listed['shelf'] == 'dropped' and listed['rating'] == 2


def test_an_edit_wins_even_after_the_book_changed_since_it_was_queued(client):
    fic, chapter = book()
    stale = fic_edit(client, fic, {'rating': 5}, revision=1)
    db = get_db()
    db.execute('UPDATE fic_chapters SET posted_at=99 WHERE id=?', (chapter,))  # a new snapshot
    db.commit()
    assert send(client, stale).status_code == 200
    assert db.execute('SELECT rating FROM fics WHERE id=?', (fic,)).fetchone()[0] == 5


def test_reshelving_to_the_same_shelf_keeps_when_it_moved(client):
    fic, _ = book()
    send(client, fic_edit(client, fic, {'shelf': 'paused'}))
    db = get_db()
    db.execute('UPDATE fics SET shelved_at=100 WHERE id=?', (fic,))
    db.commit()
    send(client, fic_edit(client, fic, {'shelf': 'paused', 'rating': 3}))
    assert db.execute('SELECT shelved_at FROM fics WHERE id=?', (fic,)).fetchone()[0] == 100


def test_folder_checklist_files_unfiles_and_claims_imported_memberships(client):
    fic, _ = book()
    keep, drop, claimed = folder('Keep'), folder('Drop'), folder('Imported')
    db = get_db()
    db.execute("INSERT INTO fic_folder_items(folder_id,fic_id,origin,created_at) VALUES (?,?,'manual',1)", (drop, fic))
    db.execute("INSERT INTO fic_folder_items(folder_id,fic_id,origin,created_at) VALUES (?,?,'import',1)", (claimed, fic))
    db.commit()
    response = send(client, fic_edit(client, fic, {'folderIds': [keep, claimed]}))
    assert response.status_code == 200
    assert response.json['change']['data']['folderIds'] == sorted([keep, claimed])
    rows = dict(db.execute('SELECT folder_id, origin FROM fic_folder_items WHERE fic_id=?', (fic,)).fetchall())
    assert rows == {keep: 'manual', claimed: 'manual'}


def test_a_deleted_folder_refuses_the_whole_edit(client):
    fic, _ = book()
    response = send(client, fic_edit(client, fic, {'folderIds': [str(ULID())], 'rating': 4}))
    assert response.status_code == 409 and response.json['conflict']
    assert get_db().execute('SELECT rating FROM fics WHERE id=?', (fic,)).fetchone()[0] is None


@pytest.mark.parametrize('data', [
    {}, {'title': 'x'}, {'rating': 6}, {'rating': True}, {'shelf': 'archived'},
    {'review': 3}, {'folderIds': 'nope'}, {'folderIds': ['not-a-ulid']},
])
def test_invalid_fic_edits_are_rejected(client, data):
    fic, _ = book()
    assert send(client, fic_edit(client, fic, data)).status_code == 400


def test_marking_a_chapter_read_and_unread_replicates_and_moves_the_unread_count(client):
    fic, chapter = book()
    db = get_db()
    # XenForo stores the forum's own "Threadmarks"; only the main story counts.
    for category in ('Threadmarks', 'Sidestory'):
        db.execute("INSERT INTO fic_chapters(id,fic_id,position,title,category,content_html,content_text,created_at)"
                   " VALUES (?,?,1,'Extra',?,'','',1)", (str(ULID()), fic, category))
    db.commit()
    initial = client.get('/api/mobile/sync?collections=fics,fic_chapter_reads').json
    assert initial['changes'][0]['data']['unreadCount'] == 2
    body = read_edit(client, fic, chapter)
    created = send(client, body)
    assert created.status_code == 200
    assert created.json['change']['id'] == chapter
    assert created.json['change']['data']['ficId'] == fic
    assert send(client, body).json == created.json
    later = client.get('/api/mobile/sync', query_string={'cursor': initial['cursor']}).json['changes']
    book_now = [c for c in later if c['collection'] == 'fics'][-1]
    assert book_now['data']['unreadCount'] == 1
    removed = send(client, read_edit(client, fic, chapter, 'delete'))
    assert removed.json['change']['deleted'] is True
    assert get_db().execute('SELECT COUNT(*) FROM fic_chapter_reads').fetchone()[0] == 0


def test_unmarking_a_chapter_never_marked_still_gets_a_receipt(client):
    fic, chapter = book()
    response = send(client, read_edit(client, fic, chapter, 'delete'))
    assert response.status_code == 200 and response.json['change']['deleted'] is True


def test_a_read_mark_for_another_books_chapter_is_refused(client):
    fic, _ = book()
    _, other_chapter = book('Other')
    response = send(client, read_edit(client, fic, other_chapter))
    assert response.status_code == 409


def test_desktop_read_marks_and_reactions_replicate_keyed_by_chapter(client):
    fic, chapter = book()
    client.post(f'/api/fanfic/{fic}/read', json={'chapterIds': [chapter], 'read': True})
    db = get_db()
    db.execute("INSERT INTO fic_chapter_reactions(chapter_id,fic_id,reaction,checked_at) VALUES (?,?,'Like',5)",
               (chapter, fic))
    db.commit()
    changes = client.get('/api/mobile/sync?collections=fic_chapter_reads,fic_chapter_reactions').json['changes']
    by_collection = {c['collection']: c for c in changes}
    assert by_collection['fic_chapter_reads']['id'] == chapter
    assert by_collection['fic_chapter_reactions']['data'] == {
        'id': chapter, 'chapterId': chapter, 'ficId': fic, 'reaction': 'Like', 'checkedAt': 5,
        'source': 'site', 'pending': None, 'pushError': None}


def test_shelf_route_validates_and_sets(client):
    fic, _ = book()
    assert client.patch(f'/api/fanfic/{fic}/shelf', json={'shelf': 'nope'}).status_code == 400
    assert client.patch(f'/api/fanfic/{str(ULID())}/shelf', json={'shelf': 'finished'}).status_code == 404
    assert client.patch(f'/api/fanfic/{fic}/shelf', json={'shelf': 'finished'}).status_code == 200
    row = get_db().execute('SELECT shelf, shelved_at FROM fics WHERE id=?', (fic,)).fetchone()
    assert row['shelf'] == 'finished' and row['shelved_at'] is not None


def like_edit(client, fic, chapter, liked=True, assumed=False):
    return dict(id=str(ULID()), epoch=epoch(client), collection='fic_chapter_reactions',
                recordId=chapter, baseRevision=0, action='update',
                data={'ficId': fic, 'liked': liked, 'assumed': assumed})


def forum_book():
    fic, chapter = book()
    db = get_db()
    db.execute("UPDATE fics SET source_url='https://forums.spacebattles.com/threads/x.5/' WHERE id=?", (fic,))
    db.execute("UPDATE fic_chapters SET source_post_id='55' WHERE id=?", (chapter,))
    db.commit()
    return fic, chapter


def test_a_like_from_the_phone_is_queued_for_the_forum(client, monkeypatch):
    from backend.fanfic import likes
    pushes = []
    monkeypatch.setattr(likes, 'start_push', lambda: pushes.append(1))
    fic, chapter = forum_book()
    body = like_edit(client, fic, chapter)
    response = send(client, body)
    assert response.status_code == 200
    data = response.json['change']['data']
    assert (data['reaction'], data['source'], data['pending']) == ('Like', 'app', 'like')
    assert pushes
    assert send(client, body).json == response.json  # a replay is the same receipt


def test_an_assumed_like_from_the_phone_queues_nothing(client, monkeypatch):
    from backend.fanfic import likes
    monkeypatch.setattr(likes, 'start_push', lambda: None)
    fic, chapter = forum_book()
    data = send(client, like_edit(client, fic, chapter, assumed=True)).json['change']['data']
    assert (data['reaction'], data['source'], data['pending']) == ('Like', 'assumed', None)


def test_a_like_names_its_book_and_must_be_a_forum_chapter(client):
    fic, chapter = book()
    other, _ = book()
    assert send(client, like_edit(client, other, chapter)).status_code == 409
    assert send(client, like_edit(client, fic, chapter)).status_code == 409  # not a forum post


def test_a_books_snapshot_says_whether_it_is_backlog(client):
    fic, _ = book()
    db = get_db()
    snapshot = lambda: db.execute(  # noqa: E731
        "SELECT payload FROM mobile_sync_changes WHERE collection='fics' AND record_id=?"
        ' ORDER BY sequence DESC LIMIT 1', (fic,)).fetchone()[0]
    import json
    assert json.loads(snapshot())['backlog'] == 0
    db.execute("INSERT INTO fic_list_passes(site, list, completed_at) VALUES ('forums.spacebattles.com','watched',1)")
    db.commit()
    assert json.loads(snapshot())['backlog'] == 1
    db.execute("INSERT INTO fic_source_lists(fic_id, list, seen_at) VALUES (?, 'watched', 1)", (fic,))
    db.commit()
    data = json.loads(snapshot())
    assert data['backlog'] == 0 and data['source_lists'] == ['watched']
