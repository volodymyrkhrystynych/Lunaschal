from ulid import ULID

from backend.db.connection import get_db


def book(title='Book'):
    db = get_db()
    fic, chapter = str(ULID()), str(ULID())
    db.execute("INSERT INTO fics(id,title,source_type,site,created_at,updated_at) VALUES (?,?,'xenforo','forums.spacebattles.com',1,1)", (fic, title))
    db.execute("INSERT INTO fic_chapters(id,fic_id,position,title,content_html,content_text,created_at,updated_at,posted_at) VALUES (?,?,0,'Chapter','','Words',1,1,20)", (chapter, fic))
    db.commit()
    return fic, chapter


def operation(client, fic, chapter, kind='favorite', previous=None, revision=0):
    epoch = client.get('/api/mobile/sync?collections=fic_bookmarks').json['epoch']
    return dict(id=str(ULID()), epoch=epoch, collection='fic_bookmarks', recordId=str(ULID()),
                baseRevision=revision, action='create', data=dict(ficId=fic, chapterId=chapter,
                type=kind, scrollPosition=0.5, previousContinueId=previous))


def send(client, body):
    return client.post('/api/mobile/operations', json=body)


def test_folder_tags_and_publication_changes_are_immutable_snapshots(client):
    fic, chapter = book()
    initial = client.get('/api/mobile/sync?collections=fics').json
    assert initial['changes'][0]['data']['site'] == 'forums.spacebattles.com'
    assert initial['changes'][0]['data']['folderIds'] == []
    db = get_db()
    folder = str(ULID())
    db.execute("INSERT INTO fic_folders(id,name,created_at,updated_at) VALUES (?,'Fantasy',1,1)", (folder,))
    db.execute('INSERT INTO fic_folder_items(folder_id,fic_id,created_at) VALUES (?,?,1)', (folder, fic))
    db.execute("INSERT INTO fic_site_tags(fic_id,name,created_at) VALUES (?,'Magic',1)", (fic,))
    db.execute('UPDATE fic_chapters SET posted_at=50 WHERE id=?', (chapter,))
    db.commit()
    changes = client.get('/api/mobile/sync', query_string={'cursor': initial['cursor']}).json['changes']
    assert changes[-1]['data']['folderIds'] == [folder]
    assert changes[-1]['data']['tags'] == ['Magic']
    assert changes[-1]['data']['latestActivity'] == 50
    assert initial['changes'][0]['data']['tags'] == []
    db.execute('DELETE FROM fic_folders WHERE id=?', (folder,))
    db.execute('DELETE FROM fic_site_tags WHERE fic_id=?', (fic,))
    db.commit()
    latest = client.get('/api/mobile/sync?collections=fics').json['changes'][0]['data']
    assert latest['folderIds'] == latest['tags'] == []
    assert 'filePath' not in latest and 'coverPath' not in latest


def test_mobile_favorite_replay_is_idempotent_and_visible_on_desktop(client):
    fic, chapter = book()
    body = operation(client, fic, chapter)
    response = send(client, body)
    assert response.status_code == 200
    assert send(client, body).json == response.json
    desktop = client.get(f'/api/fanfic/{fic}/bookmarks').json
    assert len(desktop) == 1
    assert desktop[0]['id'] == body['recordId']
    assert desktop[0]['scrollPosition'] == 0.5
    body['data']['scrollPosition'] = 0.75
    assert send(client, body).status_code == 409


def test_continue_replacement_checks_desktop_changes_and_retains_favorites(client):
    fic, chapter = book()
    favorite = send(client, operation(client, fic, chapter)).json['change']
    first = send(client, operation(client, fic, chapter, 'continue')).json['change']
    replace = operation(client, fic, chapter, 'continue', first['id'], first['revision'])
    desktop = client.post(f'/api/fanfic/{fic}/bookmarks', json=dict(chapterId=chapter, type='continue', scrollPosition=0.8)).json
    conflict = send(client, replace)
    assert conflict.status_code == 409 and conflict.json['conflict']
    assert conflict.json['current']['id'] == desktop['id']
    ids = {b['id'] for b in client.get(f'/api/fanfic/{fic}/bookmarks').json}
    assert ids == {desktop['id'], favorite['id']}
    assert send(client, replace).json == conflict.json


def test_continue_replacement_and_delete_have_durable_receipts(client):
    fic, chapter = book()
    first = send(client, operation(client, fic, chapter, 'continue')).json['change']
    second = send(client, operation(client, fic, chapter, 'continue', first['id'], first['revision'])).json['change']
    assert [b['id'] for b in client.get(f'/api/fanfic/{fic}/bookmarks').json] == [second['id']]
    body = operation(client, fic, chapter)
    body.update(action='delete', recordId=second['id'], baseRevision=second['revision'], data={})
    deleted = send(client, body)
    assert deleted.status_code == 200 and deleted.json['change']['deleted']
    assert send(client, body).json == deleted.json


def test_bookmark_rejects_wrong_chapter_invalid_position_and_old_epoch(client):
    fic, chapter = book()
    other, _ = book('Other')
    assert send(client, operation(client, other, chapter)).status_code == 409
    for position in (-1, 2, True, '0.5'):
        body = operation(client, fic, chapter)
        body['data']['scrollPosition'] = position
        assert send(client, body).status_code == 400
    body = operation(client, fic, chapter)
    body['epoch'] = str(ULID())
    assert send(client, body).status_code == 410
    assert client.get(f'/api/fanfic/{fic}/bookmarks').json == []
