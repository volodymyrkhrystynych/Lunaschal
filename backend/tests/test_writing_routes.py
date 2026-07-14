"""End-to-end tests for the writing routes: projects, chapters, notes, discussions."""


def _create_project(client, title='My Story', description=None):
    resp = client.post('/api/writing/projects', json={'title': title, 'description': description})
    assert resp.status_code == 201
    return resp.get_json()['id']


# --- Projects ---

def test_project_crud(client):
    pid = _create_project(client, 'Space Opera', 'A big space story')

    resp = client.get('/api/writing/projects')
    projects = resp.get_json()
    assert len(projects) == 1
    assert projects[0]['title'] == 'Space Opera'

    resp = client.get(f'/api/writing/projects/{pid}')
    assert resp.status_code == 200
    assert resp.get_json()['description'] == 'A big space story'

    resp = client.patch(f'/api/writing/projects/{pid}', json={'title': 'Space Saga'})
    assert resp.status_code == 200
    assert client.get(f'/api/writing/projects/{pid}').get_json()['title'] == 'Space Saga'

    resp = client.delete(f'/api/writing/projects/{pid}')
    assert resp.status_code == 200
    assert client.get('/api/writing/projects').get_json() == []


def test_create_project_requires_title(client):
    resp = client.post('/api/writing/projects', json={})
    assert resp.status_code == 400


# --- Chapters ---

def test_chapter_crud_and_ordering(client):
    pid = _create_project(client)
    c1 = client.post(f'/api/writing/projects/{pid}/chapters', json={'title': 'One'}).get_json()['id']
    c2 = client.post(f'/api/writing/projects/{pid}/chapters', json={'title': 'Two'}).get_json()['id']

    chapters = client.get(f'/api/writing/projects/{pid}/chapters').get_json()
    assert [c['title'] for c in chapters] == ['One', 'Two']
    assert [c['position'] for c in chapters] == [0, 1]
    assert all('content' not in c for c in chapters)

    resp = client.patch(f'/api/writing/chapters/{c1}', json={'content': 'It begins.'})
    assert resp.status_code == 200
    full = client.get(f'/api/writing/chapters/{c1}').get_json()
    assert full['content'] == 'It begins.'

    client.delete(f'/api/writing/chapters/{c2}')
    chapters = client.get(f'/api/writing/projects/{pid}/chapters').get_json()
    assert [c['title'] for c in chapters] == ['One']


# --- Notes ---

def test_note_crud(client):
    pid = _create_project(client)
    resp = client.post(f'/api/writing/projects/{pid}/notes', json={'title': 'Random idea'})
    assert resp.status_code == 201
    n1 = resp.get_json()['id']
    n2 = client.post(
        f'/api/writing/projects/{pid}/notes',
        json={'title': 'Hero', 'content': 'Brave.', 'docType': 'character'},
    ).get_json()['id']

    notes = client.get(f'/api/writing/projects/{pid}/notes').get_json()
    assert [n['docType'] for n in notes] == ['note', 'character']
    assert all('content' not in n for n in notes)

    full = client.get(f'/api/writing/notes/{n2}').get_json()
    assert full['content'] == 'Brave.'

    resp = client.patch(f'/api/writing/notes/{n1}', json={'docType': 'outline', 'content': 'Act 1...'})
    assert resp.status_code == 200
    full = client.get(f'/api/writing/notes/{n1}').get_json()
    assert full['docType'] == 'outline'
    assert full['content'] == 'Act 1...'

    client.delete(f'/api/writing/notes/{n2}')
    notes = client.get(f'/api/writing/projects/{pid}/notes').get_json()
    assert [n['id'] for n in notes] == [n1]


def test_create_note_requires_title(client):
    pid = _create_project(client)
    resp = client.post(f'/api/writing/projects/{pid}/notes', json={'content': 'no title'})
    assert resp.status_code == 400


# --- Discussions ---

def test_discussion_create_and_list(client):
    pid = _create_project(client)
    other_pid = _create_project(client, 'Other Story')

    resp = client.post(f'/api/writing/projects/{pid}/conversations', json={})
    assert resp.status_code == 201
    d1 = resp.get_json()['id']
    d2 = client.post(
        f'/api/writing/projects/{other_pid}/conversations', json={'title': 'Plot talk'}
    ).get_json()['id']

    discussions = client.get(f'/api/writing/projects/{pid}/conversations').get_json()
    assert [d['id'] for d in discussions] == [d1]
    assert discussions[0]['title'] == 'New Discussion'

    other = client.get(f'/api/writing/projects/{other_pid}/conversations').get_json()
    assert [d['title'] for d in other] == ['Plot talk']
    assert d2 in [d['id'] for d in other]


def test_discussion_rename_and_delete_via_chat_routes(client):
    pid = _create_project(client)
    did = client.post(f'/api/writing/projects/{pid}/conversations', json={}).get_json()['id']

    resp = client.patch(f'/api/chat/conversations/{did}/title', json={'title': 'Villain arc'})
    assert resp.status_code == 200
    discussions = client.get(f'/api/writing/projects/{pid}/conversations').get_json()
    assert discussions[0]['title'] == 'Villain arc'

    client.delete(f'/api/chat/conversations/{did}')
    assert client.get(f'/api/writing/projects/{pid}/conversations').get_json() == []


def test_chat_list_excludes_discussions(client):
    pid = _create_project(client)
    client.post(f'/api/writing/projects/{pid}/conversations', json={})
    plain = client.post('/api/chat/conversations', json={'title': 'General chat'}).get_json()['id']

    convs = client.get('/api/chat/conversations').get_json()
    assert [c['id'] for c in convs] == [plain]


def test_delete_project_deletes_discussions_and_messages(client):
    pid = _create_project(client)
    did = client.post(f'/api/writing/projects/{pid}/conversations', json={}).get_json()['id']
    client.post(f'/api/chat/conversations/{did}/messages', json={'role': 'user', 'content': 'hi'})

    resp = client.delete(f'/api/writing/projects/{pid}')
    assert resp.status_code == 200
    # Conversation is gone (get_conversation returns null for missing rows)
    assert client.get(f'/api/chat/conversations/{did}').get_json() is None
    assert client.get('/api/chat/conversations').get_json() == []
