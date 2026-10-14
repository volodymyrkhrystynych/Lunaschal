"""A coding agent's learnings files → lessons → cards, and retiring a card."""
import struct
import time

import pytest

from backend.ai import learning_lessons, service
from backend.db.connection import get_db
from backend.learning import lessons

DAY_FILE = """# 2026-10-12

## Recursive functions need a depth limit
- **Failure it prevents:** a cycle in the data recurses forever.
- **When it doesn't apply:** a tree the code built itself.

## Retries need a cap
- **Failure it prevents:** hammering a dead dependency.
"""


@pytest.fixture
def model(monkeypatch):
    """Stub both model calls; record what the judge was shown."""
    calls = {'judge': [], 'generate': 0}
    verdict = {'index': None, 'reason': ''}

    def judge(title, body, candidates):
        calls['judge'].append(candidates)
        return verdict['index'], verdict['reason']

    def generate(title, body):
        calls['generate'] += 1
        return [{'question': f'Scenario for {title}?', 'answer': 'Do the thing.'}]

    monkeypatch.setattr(learning_lessons, 'judge_coverage', judge)
    monkeypatch.setattr(learning_lessons, 'generate_lesson_cards', generate)
    monkeypatch.setattr(lessons, 'embed_answer', lambda text: None)
    return {'calls': calls, 'verdict': verdict}


def _lessons(client):
    return client.get('/api/learning/lessons').json['lessons']


# ------------------------------------------------------------------ parsing

def test_parse_splits_on_level_two_headings_and_drops_the_date():
    parsed = lessons.parse_lessons(DAY_FILE)
    assert [l['title'] for l in parsed] == [
        'Recursive functions need a depth limit', 'Retries need a cap']
    assert 'cycle in the data' in parsed[0]['body']
    assert '2026-10-12' not in parsed[0]['body']


def test_parse_without_headings_is_one_lesson_titled_by_its_first_line():
    parsed = lessons.parse_lessons('- Always bound recursion\nCycles happen in user data.')
    assert parsed == [{'title': 'Always bound recursion',
                       'body': 'Cycles happen in user data.'}]


def test_parse_of_nothing_is_nothing():
    assert lessons.parse_lessons('') == []
    assert lessons.parse_lessons('# 2026-10-12\n\n') == []


def test_hash_ignores_whitespace_and_case_only():
    a = lessons.lesson_hash('Depth limit', 'Cycles  recurse\nforever')
    assert a == lessons.lesson_hash('depth LIMIT', 'cycles recurse forever')
    assert a != lessons.lesson_hash('Depth limit', 'Cycles recurse sometimes')


# ------------------------------------------------------------------ importing

def test_import_cards_each_new_lesson(client, model, run_jobs_sync):
    r = client.post('/api/learning/lessons/import',
                    json={'text': DAY_FILE, 'sourceName': '2026-10-12.md'})
    assert r.status_code == 200
    assert r.json['added'] == 2

    rows = _lessons(client)
    assert {l['status'] for l in rows} == {'carded'}
    assert all(l['cardCount'] == 1 for l in rows)
    assert rows[0]['sourceName'] == '2026-10-12.md'
    assert 'contentHash' not in rows[0]

    queue = client.get('/api/learning/queue').json
    assert len(queue) == 2
    assert {c['sourceType'] for c in queue} == {'agent_lesson'}
    assert all(c['tags'] == ['lesson'] for c in queue)


def test_reimporting_a_grown_file_adds_only_the_new_lesson(client, model, run_jobs_sync):
    client.post('/api/learning/lessons/import', json={'text': DAY_FILE})
    grown = DAY_FILE + '\n## Close what you open\nFile handles leak otherwise.\n'
    r = client.post('/api/learning/lessons/import', json={'text': grown})
    assert r.json['added'] == 1
    assert r.json['alreadyImported'] == 2
    assert model['calls']['generate'] == 3


def test_import_rejects_empty_text_and_unknown_folder(client, model):
    assert client.post('/api/learning/lessons/import', json={'text': '  '}).status_code == 400
    r = client.post('/api/learning/lessons/import',
                    json={'text': DAY_FILE, 'folderId': 'nope'})
    assert r.status_code == 400


def test_import_defaults_to_the_configured_folder(client, model, run_jobs_sync):
    folder = client.post('/api/learning/folders', json={'name': 'Engineering'}).json['id']
    client.put('/api/learning/lessons/config', json={'folderId': folder})
    client.post('/api/learning/lessons/import', json={'text': DAY_FILE})
    assert {c['folderId'] for c in client.get('/api/learning/queue').json} == {folder}


# ------------------------------------------------------------------ the judge

def test_a_covered_lesson_makes_no_cards_and_says_why(client, model, run_jobs_sync, monkeypatch):
    existing = client.post('/api/learning/cards',
                           json={'question': 'Q?', 'answer': 'Bound recursion.'}).json['id']
    monkeypatch.setattr(lessons, '_candidates',
                        lambda db, lesson_id, emb: [{'id': existing, 'question': 'Q?',
                                                     'answer': 'Bound recursion.'}])
    model['verdict'].update(index=0, reason='Same principle.')

    client.post('/api/learning/lessons/import',
                json={'text': '## Depth limits\nCycles recurse forever.'})
    [row] = _lessons(client)
    assert row['status'] == 'covered'
    assert row['coveredBy'] == existing
    assert row['coveredQuestion'] == 'Q?'
    assert row['reason'] == 'Same principle.'
    assert model['calls']['generate'] == 0

    # Overriding the verdict cards it without asking the judge again.
    judged = len(model['calls']['judge'])
    assert client.post(f"/api/learning/lessons/{row['id']}/force").status_code == 200
    [row] = _lessons(client)
    assert row['status'] == 'carded'
    assert row['coveredBy'] is None
    assert len(model['calls']['judge']) == judged


def test_force_on_a_lesson_still_processing_is_409(client, model, queued_jobs):
    client.post('/api/learning/lessons/import', json={'text': '## A\nB'})
    [row] = _lessons(client)
    assert row['status'] == 'pending'
    assert client.post(f"/api/learning/lessons/{row['id']}/force").status_code == 409


def test_a_finished_lesson_is_not_carded_twice_on_replay(client, model, run_jobs_sync):
    client.post('/api/learning/lessons/import', json={'text': '## A\nB'})
    [row] = _lessons(client)
    lessons.process_lesson(row['id'])
    assert model['calls']['generate'] == 1


def test_a_model_failure_marks_the_lesson(client, model, run_jobs_sync, monkeypatch):
    def boom(title, body):
        raise RuntimeError('llama-server is down')
    monkeypatch.setattr(learning_lessons, 'generate_lesson_cards', boom)
    client.post('/api/learning/lessons/import', json={'text': '## A\nB'})
    [row] = _lessons(client)
    assert row['status'] == 'error'
    assert 'llama-server' in row['error']


def test_a_paused_model_leaves_the_lesson_pending(client, model, run_jobs_sync, monkeypatch):
    def paused(title, body):
        raise service.InferencePaused()
    monkeypatch.setattr(learning_lessons, 'generate_lesson_cards', paused)
    client.post('/api/learning/lessons/import', json={'text': '## A\nB'})
    [row] = _lessons(client)
    assert row['status'] == 'pending'
    job = get_db().execute("SELECT status FROM llm_jobs WHERE kind=?",
                           (lessons.JOB_KIND,)).fetchone()
    assert job['status'] == 'pending'


def _vec(*xs):
    return struct.pack(f'{len(xs)}f', *xs)


def _card(id, answer, embedding, *, state='active', source_type=None, source_id=None):
    now = int(time.time())
    get_db().execute(
        'INSERT INTO learning_cards (id, question, answer, state, answer_embedding,'
        ' source_type, source_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (id, f'Q {id}', answer, state, embedding, source_type, source_id, now, now),
    )
    get_db().commit()


def test_candidates_are_the_nearest_live_or_queued_cards(client):
    _card('close', 'a', _vec(1, 0.1), state='pending')
    _card('closer', 'b', _vec(1, 0))
    _card('far', 'c', _vec(0, 1))
    _card('retired', 'd', _vec(1, 0), state='retired')
    _card('own', 'e', _vec(1, 0), source_type=lessons.SOURCE_TYPE, source_id='L1')
    ids = [c['id'] for c in lessons._candidates(get_db(), 'L1', _vec(1, 0))]
    assert ids == ['closer', 'close']
    assert lessons._candidates(get_db(), 'L1', None) == []


def test_judge_maps_its_answer_onto_the_candidates(monkeypatch):
    replies = iter([{'card': 2, 'reason': 'same'}, {'card': 0, 'reason': 'new'}])
    monkeypatch.setattr(learning_lessons, 'chat_json', lambda *a, **k: next(replies))
    cands = [{'question': 'q1', 'answer': 'a1'}, {'question': 'q2', 'answer': 'a2'}]
    assert learning_lessons.judge_coverage('t', 'b', cands) == (1, 'same')
    assert learning_lessons.judge_coverage('t', 'b', cands) == (None, 'new')
    # No candidates, no model call.
    assert learning_lessons.judge_coverage('t', 'b', []) == (None, '')


# ------------------------------------------------------------------ the inbox

def test_scan_reports_an_unset_or_missing_inbox(client, tmp_path):
    assert client.post('/api/learning/lessons/scan').json['state'] == 'unset'
    client.put('/api/learning/lessons/config', json={'inboxPath': str(tmp_path / 'gone')})
    assert client.post('/api/learning/lessons/scan').json['state'] == 'missing'
    assert not (tmp_path / 'gone').exists()


def test_scan_imports_markdown_files_once_and_leaves_them_alone(
        client, model, run_jobs_sync, tmp_path):
    (tmp_path / '2026-10-11.md').write_text(DAY_FILE)
    (tmp_path / 'notes.txt').write_text('## Not a learnings file\nignored')
    r = client.put('/api/learning/lessons/config', json={'inboxPath': str(tmp_path)})
    assert r.json['inboxState'] == 'ok'

    first = client.post('/api/learning/lessons/scan').json
    assert (first['files'], first['added']) == (1, 2)
    again = client.post('/api/learning/lessons/scan').json
    assert (again['added'], again['alreadyImported']) == (0, 2)
    assert (tmp_path / '2026-10-11.md').read_text() == DAY_FILE
    assert {l['source'] for l in _lessons(client)} == {'inbox'}


# ------------------------------------------------------------------ retiring

def _active_card(client):
    return client.post('/api/learning/cards', json={'question': 'Q?', 'answer': 'A.'}).json['id']


def test_retire_takes_a_card_out_of_review_and_its_session(client):
    card = _active_card(client)
    client.post('/api/learning/attempts',
                json={'id': 'att1', 'cardId': card, 'mode': 'skipped'})
    assert client.post(f'/api/learning/cards/{card}/retire').status_code == 200

    assert card not in [c['id'] for c in client.get('/api/learning/due').json]
    assert client.get('/api/learning/attempts').json == []
    retired = client.get('/api/learning/cards?state=retired').json
    assert [(c['id'], c['superseded']) for c in retired] == [(card, False)]
    # Replaying the retire (offline queue) is harmless.
    assert client.post(f'/api/learning/cards/{card}/retire').status_code == 200


def test_unretire_puts_it_back(client):
    card = _active_card(client)
    client.post(f'/api/learning/cards/{card}/retire')
    assert client.post(f'/api/learning/cards/{card}/unretire').status_code == 200
    assert card in [c['id'] for c in client.get('/api/learning/cards').json]


def test_a_card_a_revision_replaced_cannot_be_unretired(client):
    card = _active_card(client)
    _card('successor', 'A2.', None)
    get_db().execute('UPDATE learning_cards SET revised_from=? WHERE id=?', (card, 'successor'))
    get_db().execute("UPDATE learning_cards SET state='retired' WHERE id=?", (card,))
    get_db().commit()
    assert client.get('/api/learning/cards?state=retired').json[0]['superseded'] is True
    assert client.post(f'/api/learning/cards/{card}/unretire').status_code == 409


def test_a_pending_card_cannot_be_retired(client):
    get_db().execute(
        "INSERT INTO learning_cards (id, question, answer, state, created_at, updated_at)"
        " VALUES ('p', 'Q', 'A', 'pending', 0, 0)")
    get_db().commit()
    assert client.post('/api/learning/cards/p/retire').status_code == 404
