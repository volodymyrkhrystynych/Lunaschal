import io
import json
import sqlite3

import pytest
from PIL import Image
from ulid import ULID

from backend.db.connection import get_db, init_db
from backend.mobile_sync.drawings import apply


@pytest.fixture(autouse=True)
def paper_root(monkeypatch, tmp_path):
    root = tmp_path / 'paper'
    monkeypatch.setenv('PAPER_ROOT', str(root))
    return root


def metadata(client):
    epoch = client.get('/api/mobile/sync', query_string={'collections': 'papers'}).json['epoch']
    return dict(id=str(ULID()), epoch=epoch, paperId=str(ULID()), pageId=str(ULID()),
                baseRevision=0, title='Offline sketch', format='pencilkit-v1')


def preview():
    data = io.BytesIO()
    Image.new('RGBA', (1240, 1754), (255, 255, 255, 255)).save(data, 'PNG')
    return data.getvalue()


def save(client, body, ink=b'native-original', png=None):
    return client.post('/api/mobile/drawings', data={
        'metadata': json.dumps(body), 'ink': (io.BytesIO(ink), 'original.drawing'),
        'preview': (io.BytesIO(preview() if png is None else png), 'preview.png'),
    })


def revision(reply):
    return next(c['revision'] for c in reply.json['changes'] if c['collection'] == 'paper_native_ink')


def test_save_replay_download_and_restart(client):
    body = metadata(client)
    first = save(client, body)
    assert first.status_code == 200
    assert save(client, body).json == first.json
    assert save(client, body, ink=b'changed').status_code == 409
    content = client.get(f"/api/paper/pages/{body['pageId']}").json
    assert content['nativeInk'] is True
    assert client.get(content['imageUrl']).data == preview()
    item = client.get('/api/mobile/media?collection=paper_native_ink').json['items'][0]
    assert client.get(item['url']).data == b'native-original'
    assert 'path' not in json.dumps(first.json).lower()
    init_db()
    assert save(client, body).json == first.json


def test_stale_save_and_delete_preserve_receipts_without_resurrection(client):
    body = metadata(client)
    first = save(client, body)
    next_body = {**body, 'id': str(ULID()), 'baseRevision': revision(first)}
    assert save(client, next_body, ink=b'new').status_code == 200
    stale = {**next_body, 'id': str(ULID())}
    refused = save(client, stale, ink=b'other device')
    assert refused.status_code == 409 and refused.json['conflict']
    assert save(client, stale, ink=b'other device').json == refused.json
    client.delete(f"/api/paper/{body['paperId']}")
    assert save(client, body).json == first.json  # receipt, not a new write
    assert save(client, {**body, 'id': str(ULID())}).status_code == 409
    assert get_db().execute('SELECT count(*) FROM paper_native_ink').fetchone()[0] == 0
    changes = client.get('/api/mobile/sync?collections=paper_native_ink').json['changes']
    assert changes[0]['deleted'] is True


def test_web_page_cannot_be_converted_and_native_page_cannot_be_overwritten(client):
    body = metadata(client)
    paper = client.post('/api/paper').json['id']
    page = client.get(f'/api/paper/{paper}').json['pages'][0]['id']
    change = client.get('/api/mobile/sync?collections=paper_pages').json['changes'][0]
    assert save(client, {**body, 'id': str(ULID()), 'paperId': paper, 'pageId': page,
                         'baseRevision': change['revision']}).status_code == 409
    assert save(client, body).status_code == 200
    assert client.put(f"/api/paper/pages/{body['pageId']}", data={'strokes': '[]'}).status_code == 409
    assert client.post(f"/api/paper/pages/{body['pageId']}/images").status_code == 409


def test_ack_failure_rolls_back_domain_writes_and_existing_files(client):
    body = metadata(client)
    first = save(client, body)
    url = client.get(f"/api/paper/pages/{body['pageId']}").json['imageUrl']
    before = client.get(url).data
    db = get_db()
    db.execute("CREATE TRIGGER refuse_drawing_ack BEFORE INSERT ON mobile_sync_operations BEGIN SELECT RAISE(ABORT,'disk error'); END")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        apply({**body, 'id': str(ULID()), 'baseRevision': revision(first)}, io.BytesIO(b'new'), io.BytesIO(preview()))
    assert client.get(url).data == before
    assert client.get('/api/mobile/media?collection=paper_native_ink').json['items'][0]['size'] == len(b'native-original')


@pytest.mark.parametrize('patch', [{'id': '../escape'}, {'baseRevision': True}, {'baseRevision': -1},
                                  {'format': 'svg'}, {'title': ['wrong']}, {'pageId': None}])
def test_invalid_metadata_has_no_writes(client, patch):
    assert save(client, {**metadata(client), **patch}).status_code == 400
    assert get_db().execute('SELECT count(*) FROM papers').fetchone()[0] == 0


def test_invalid_preview_and_wrong_epoch(client):
    body = metadata(client)
    assert save(client, body, png=b'not png').status_code == 400
    assert save(client, body, ink=b'').status_code == 400
    assert save(client, {**body, 'epoch': 'old-server'}).status_code == 410
    assert get_db().execute('SELECT count(*) FROM papers').fetchone()[0] == 0


def test_symlink_into_another_paper_is_rejected(client, paper_root):
    body = metadata(client)
    other = paper_root / str(ULID())
    other.mkdir(parents=True)
    (paper_root / body['paperId']).symlink_to(other, target_is_directory=True)
    assert save(client, body).status_code == 400
    assert list(other.iterdir()) == []


def test_repeated_saves_keep_current_and_previous_original_only(client, paper_root):
    body = metadata(client)
    last = save(client, body)
    for index in range(5):
        body = {**body, 'id': str(ULID()), 'baseRevision': revision(last)}
        last = save(client, body, ink=f'ink {index}'.encode())
        assert last.status_code == 200
    files = list((paper_root / body['paperId']).glob('*.drawing'))
    assert sorted(file.read_bytes() for file in files) == [b'ink 3', b'ink 4']
