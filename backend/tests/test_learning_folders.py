"""Folder CRUD, provider binding, and card/tag filtering."""


def _make_card(client, question='Q?', answer='A.', **extra):
    r = client.post('/api/learning/cards', json={'question': question, 'answer': answer, **extra})
    assert r.status_code == 201
    return r.json['id']


def _insert_mcp_server(name='ctx7'):
    from backend.db.connection import get_db
    from ulid import ULID
    id = str(ULID())
    get_db().execute(
        "INSERT INTO mcp_servers(id, name, transport, command, created_at, updated_at)"
        " VALUES (?,?,'stdio','npx',1,1)",
        (id, name),
    )
    get_db().commit()
    return id


def test_folder_crud(client):
    r = client.post('/api/learning/folders', json={'name': 'Python'})
    assert r.status_code == 201
    fid = r.json['id']

    assert client.post('/api/learning/folders', json={'name': 'Python'}).status_code == 400
    assert client.post('/api/learning/folders', json={}).status_code == 400

    folders = client.get('/api/learning/folders').json
    assert [f['name'] for f in folders] == ['Python']
    assert folders[0]['activeCount'] == 0

    assert client.patch(f'/api/learning/folders/{fid}', json={'name': 'Py'}).status_code == 200
    assert client.get('/api/learning/folders').json[0]['name'] == 'Py'
    assert client.patch('/api/learning/folders/nope', json={'name': 'x'}).status_code == 404


def test_folder_counts_and_card_filter(client):
    fid = client.post('/api/learning/folders', json={'name': 'F'}).json['id']
    in_folder = _make_card(client, folderId=fid)
    _make_card(client, question='Other?', answer='Other.')

    folder = client.get('/api/learning/folders').json[0]
    assert folder['activeCount'] == 1
    assert folder['dueCount'] == 1  # manual cards are due immediately

    cards = client.get(f'/api/learning/cards?folderId={fid}').json
    assert [c['id'] for c in cards] == [in_folder]


def test_folder_delete_orphans_cards(client):
    fid = client.post('/api/learning/folders', json={'name': 'F'}).json['id']
    cid = _make_card(client, folderId=fid)
    assert client.delete(f'/api/learning/folders/{fid}').status_code == 200
    card = client.get(f'/api/learning/cards/{cid}').json
    assert card['folderId'] is None


def test_evidence_provider_binding(client):
    fid = client.post('/api/learning/folders', json={'name': 'F'}).json['id']
    assert client.patch(
        f'/api/learning/folders/{fid}', json={'evidenceProviderId': 'nope'}
    ).status_code == 400

    sid = _insert_mcp_server()
    assert client.patch(
        f'/api/learning/folders/{fid}', json={'evidenceProviderId': sid}
    ).status_code == 200
    folder = client.get('/api/learning/folders').json[0]
    assert folder['evidenceProviderId'] == sid
    assert folder['evidenceProviderName'] == 'ctx7'

    # Unbind explicitly with null.
    assert client.patch(
        f'/api/learning/folders/{fid}', json={'evidenceProviderId': None}
    ).status_code == 200
    assert client.get('/api/learning/folders').json[0]['evidenceProviderId'] is None


def test_tag_normalization_and_filtering(client):
    a = _make_card(client, tags=['Python', 'python', ' AI '])
    _make_card(client, question='Q2?', answer='A2.', tags=['other'])

    card = client.get(f'/api/learning/cards/{a}').json
    assert card['tags'] == ['python', 'ai']

    filtered = client.get('/api/learning/cards?tag=PYTHON').json
    assert [c['id'] for c in filtered] == [a]

    tags = client.get('/api/learning/tags').json
    assert {t['name'] for t in tags} == {'python', 'ai', 'other'}


def test_card_content_edit_rules(client):
    cid = _make_card(client)  # manual create → active
    r = client.patch(f'/api/learning/cards/{cid}', json={'answer': 'new'})
    assert r.status_code == 400  # active answers change via revise only
    assert client.patch(f'/api/learning/cards/{cid}', json={'tags': ['t']}).status_code == 200

    assert client.delete(f'/api/learning/cards/{cid}').status_code == 200
    assert client.get(f'/api/learning/cards/{cid}').status_code == 404
