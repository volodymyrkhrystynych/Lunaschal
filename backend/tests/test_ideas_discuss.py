"""The tool-using discussion endpoint, plan generation, and list stats."""
import json
from types import SimpleNamespace

import pytest

from backend.db.connection import get_db
from backend.research import plan as plan_mod


@pytest.fixture(autouse=True)
def ai_on(monkeypatch):
    import backend.routes.ideas as routes
    monkeypatch.setattr(routes, 'is_ai_configured', lambda: True)


def _idea(client, title='Global search'):
    return client.post('/api/ideas', json={'title': title, 'rawContent': 'search everything'}).get_json()['id']


def _sse(response):
    """Parse an SSE body into a list of payloads."""
    events = []
    for line in response.get_data(as_text=True).splitlines():
        if not line.startswith('data: '):
            continue
        payload = line[6:]
        if payload == '[DONE]':
            break
        events.append(json.loads(payload))
    return events


def _stub_agent(monkeypatch, steps=None, sources=None):
    from backend.research import agent
    steps = steps if steps is not None else [
        {'tool': 'web_search', 'arg': 'global search', 'ok': True, 'count': 3}
    ]
    sources = sources if sources is not None else [{'url': 'https://ex.com/a', 'title': 'A'}]

    def fake(system, user, **kw):
        for step in steps:
            yield ('step', step)
        yield ('result', {
            'messages': [{'role': 'user', 'content': user}],
            'steps': steps, 'sources': sources, 'turns': 2, 'truncated': False,
        })

    monkeypatch.setattr(agent, 'gather_events', fake)


def _stub_stream(monkeypatch, chunks=('Use ', 'FTS5.')):
    import backend.ai.llm as llm
    monkeypatch.setattr(llm, 'chat_stream_deltas', lambda messages: iter(chunks))


# --- Conversations ---

def test_create_and_list_an_idea_conversation(client):
    idea_id = _idea(client)
    r = client.post(f'/api/ideas/{idea_id}/conversations', json={})
    assert r.status_code == 201
    conversations = client.get(f'/api/ideas/{idea_id}/conversations').get_json()
    assert len(conversations) == 1
    assert conversations[0]['ideaId'] == idea_id


def test_creating_a_conversation_for_a_missing_idea_is_404(client):
    assert client.post('/api/ideas/nope/conversations', json={}).status_code == 404


def test_idea_conversations_do_not_leak_into_the_chat_tab(client):
    """The regression this whole discriminator exists to prevent."""
    idea_id = _idea(client)
    client.post(f'/api/ideas/{idea_id}/conversations', json={})

    assert client.get('/api/chat/conversations').get_json() == []
    assert client.get('/api/chat/today').get_json() is None
    assert client.get('/api/chat/journal-conversations').get_json() == []


def test_the_briefing_does_not_adopt_an_idea_conversation(client):
    from backend.day_boundary import day_key_for
    idea_id = _idea(client)
    conversation_id = client.post(
        f'/api/ideas/{idea_id}/conversations', json={}
    ).get_json()['id']
    # Even if an idea conversation somehow carried today's day_key, the
    # briefing must create its own rather than posting into the idea thread.
    get_db().execute(
        'UPDATE conversations SET day_key=? WHERE id=?', (day_key_for(), conversation_id)
    )
    get_db().commit()

    r = client.post('/api/chat/conversations')
    assert r.get_json()['id'] != conversation_id


def test_the_title_sweep_skips_idea_conversations(client):
    from backend.day_boundary import day_key_for
    from backend.chat_title_scheduler import run_title_sweep

    idea_id = _idea(client)
    conversation_id = client.post(
        f'/api/ideas/{idea_id}/conversations', json={}
    ).get_json()['id']
    get_db().execute(
        'UPDATE conversations SET day_key=? WHERE id=?', (day_key_for(), conversation_id)
    )
    get_db().execute(
        'INSERT INTO messages(id, conversation_id, role, content, created_at)'
        " VALUES ('m1', ?, 'user', 'hello', 1)",
        (conversation_id,),
    )
    get_db().commit()

    assert run_title_sweep() == 0


# --- Discussion ---

def test_discuss_streams_tool_events_then_the_answer(client, monkeypatch):
    _stub_agent(monkeypatch)
    _stub_stream(monkeypatch)
    idea_id = _idea(client)
    conversation_id = client.post(
        f'/api/ideas/{idea_id}/conversations', json={}
    ).get_json()['id']

    r = client.post(f'/api/ideas/{idea_id}/discuss',
                    json={'conversationId': conversation_id, 'message': 'how do others do it?'})
    assert r.status_code == 200
    events = _sse(r)

    assert events[0]['tool'] == 'web_search'
    assert ''.join(e['content'] for e in events if 'content' in e) == 'Use FTS5.'
    done = [e for e in events if e.get('done')][0]
    assert done['sources'] == [{'url': 'https://ex.com/a', 'title': 'A'}]


def test_discuss_persists_both_turns_with_the_tool_trace(client, monkeypatch):
    _stub_agent(monkeypatch)
    _stub_stream(monkeypatch)
    idea_id = _idea(client)
    conversation_id = client.post(
        f'/api/ideas/{idea_id}/conversations', json={}
    ).get_json()['id']
    # SSE generators are lazy — the body has to be read for the route to run.
    _sse(client.post(f'/api/ideas/{idea_id}/discuss',
                     json={'conversationId': conversation_id, 'message': 'question?'}))

    rows = get_db().execute(
        'SELECT role, content, metadata FROM messages WHERE conversation_id=? ORDER BY created_at',
        (conversation_id,),
    ).fetchall()
    assert [r['role'] for r in rows] == ['user', 'assistant']
    assert rows[0]['content'] == 'question?'
    assert rows[1]['content'] == 'Use FTS5.'
    metadata = json.loads(rows[1]['metadata'])
    assert metadata['agent'] == 'ideas'
    assert metadata['steps'][0]['tool'] == 'web_search'
    assert metadata['sources'][0]['url'] == 'https://ex.com/a'


def test_the_question_survives_a_failure_mid_answer(client, monkeypatch):
    """The user's turn is persisted before streaming starts."""
    _stub_agent(monkeypatch)
    import backend.ai.llm as llm

    def boom(messages):
        raise RuntimeError('llama-server died')
        yield  # pragma: no cover

    monkeypatch.setattr(llm, 'chat_stream_deltas', boom)
    idea_id = _idea(client)
    conversation_id = client.post(
        f'/api/ideas/{idea_id}/conversations', json={}
    ).get_json()['id']

    events = _sse(client.post(f'/api/ideas/{idea_id}/discuss',
                              json={'conversationId': conversation_id, 'message': 'q'}))
    assert any('error' in e for e in events)

    rows = get_db().execute(
        'SELECT role FROM messages WHERE conversation_id=?', (conversation_id,)
    ).fetchall()
    assert [r['role'] for r in rows] == ['user']


def test_discuss_releases_the_priority_mark(client, monkeypatch):
    from backend.ai import priority
    priority.reset()
    _stub_agent(monkeypatch)
    _stub_stream(monkeypatch)
    idea_id = _idea(client)
    conversation_id = client.post(
        f'/api/ideas/{idea_id}/conversations', json={}
    ).get_json()['id']

    _sse(client.post(f'/api/ideas/{idea_id}/discuss',
                     json={'conversationId': conversation_id, 'message': 'q'}))
    assert not priority.active()


def test_discuss_validates_its_input(client):
    idea_id = _idea(client)
    assert client.post(f'/api/ideas/{idea_id}/discuss', json={}).status_code == 400
    assert client.post(f'/api/ideas/{idea_id}/discuss',
                       json={'conversationId': 'c', 'message': '  '}).status_code == 400
    assert client.post('/api/ideas/nope/discuss',
                       json={'conversationId': 'c', 'message': 'q'}).status_code == 404


def test_discuss_rejects_a_conversation_belonging_to_another_idea(client):
    """A conversationId must actually belong to this idea — otherwise a stale
    or mismatched id would append idea-discussion turns to someone else's
    conversation (another idea's, or a general chat/Writing one)."""
    idea_a = _idea(client, title='A')
    idea_b = _idea(client, title='B')
    conversation_id = client.post(
        f'/api/ideas/{idea_a}/conversations', json={}
    ).get_json()['id']

    r = client.post(f'/api/ideas/{idea_b}/discuss',
                     json={'conversationId': conversation_id, 'message': 'q'})
    assert r.status_code == 404
    assert get_db().execute(
        'SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?', (conversation_id,)
    ).fetchone()['n'] == 0


def test_discuss_needs_ai(client, monkeypatch):
    import backend.routes.ideas as routes
    monkeypatch.setattr(routes, 'is_ai_configured', lambda: False)
    assert client.post('/api/ideas/x/discuss',
                       json={'conversationId': 'c', 'message': 'q'}).status_code == 400


# --- Plan rendering (pure) ---

FULL_SPEC = {
    'summary': 'Add global search.',
    'goals': ['One search box'],
    'nonGoals': ['Fuzzy matching'],
    'dataModel': [{'table': 'search_index', 'purpose': 'FTS', 'columns': ['id', 'body']}],
    'api': [{'method': 'get', 'path': '/api/search', 'purpose': 'Query'}],
    'frontend': [{'file': 'src/components/Search.tsx', 'purpose': 'The box'}],
    'technicalConsiderations': [{'topic': 'FTS5', 'note': 'Use external content tables.'}],
    'phases': ['Backend', 'Frontend'],
    'risks': [{'risk': 'Index size', 'mitigation': 'Prune'}],
    'testPlan': ['Query ranking'],
}


def test_render_plan_covers_every_section():
    md = plan_mod.render_plan_markdown(
        'Global search', FULL_SPEC,
        evidence=[{'kind': 'table', 'ref': 'journal_fts', 'file': 'backend/db/schema.sql', 'line': 12}],
        answered=[{'question': 'Which engine?', 'answer': 'FTS5'}],
        open_questions=[{'question': 'Rank blending?'}],
        sources=[{'url': 'https://ex.com/a', 'title': 'A'}],
    )
    assert md.startswith('# Global search')
    for heading in ('## Goals', '## Non-goals', '## What already exists',
                    '## Decisions already made', '## Data model', '## API',
                    '## Frontend', '## Technical considerations',
                    '## Suggested phases', '## Risks', '## Tests',
                    '## Open questions', '## Sources'):
        assert heading in md, heading
    assert '`GET /api/search`' in md
    assert '`backend/db/schema.sql:12`' in md


def test_render_plan_omits_missing_sections_rather_than_emptying_them():
    md = plan_mod.render_plan_markdown('Bare', {'summary': 'Just a summary.'})
    assert '## Data model' not in md
    assert '## Open questions' not in md
    assert md.strip().endswith('Just a summary.')


def test_render_plan_handles_an_empty_spec():
    assert plan_mod.render_plan_markdown('Empty', {}).strip() == '# Empty'
    assert plan_mod.render_plan_markdown('', None).startswith('# Untitled idea')


def test_phases_the_model_already_numbered_are_not_numbered_twice():
    """Live runs come back with "1. Database: ..." about half the time, and the
    renderer numbers them too — the first real plan read "1. 1. Database:"."""
    md = plan_mod.render_plan_markdown('Numbered', {
        'phases': ['1. Database: add the tables',
                   '2) Backend: the blueprint',
                   'Phase 3 — Frontend: the views',
                   'Ship it'],
    })
    assert '1. Database: add the tables' in md
    assert '2. Backend: the blueprint' in md
    assert '3. Frontend: the views' in md
    assert '4. Ship it' in md
    assert '1. 1.' not in md


def test_a_phase_that_merely_starts_with_a_number_keeps_it():
    md = plan_mod.render_plan_markdown('Numbered', {'phases': ['2FA rollout']})
    assert '1. 2FA rollout' in md


# --- Plan generation ---

def test_create_plan_stores_a_version_and_advances_status(client, monkeypatch):
    monkeypatch.setattr(plan_mod, 'generate_spec', lambda prompt: FULL_SPEC)
    idea_id = _idea(client)

    r = client.post(f'/api/ideas/{idea_id}/plan')
    assert r.status_code == 201
    body = r.get_json()
    assert body['version'] == 1
    assert '## Data model' in body['content']
    assert client.get(f'/api/ideas/{idea_id}').get_json()['status'] == 'planned'


def test_regenerating_appends_a_version_and_keeps_the_old_one(client, monkeypatch):
    monkeypatch.setattr(plan_mod, 'generate_spec', lambda prompt: FULL_SPEC)
    idea_id = _idea(client)
    first = client.post(f'/api/ideas/{idea_id}/plan').get_json()
    second = client.post(f'/api/ideas/{idea_id}/plan').get_json()

    assert (first['version'], second['version']) == (1, 2)
    versions = [p['version'] for p in client.get(f'/api/ideas/{idea_id}/plans').get_json()]
    assert versions == [2, 1]
    assert client.get(f"/api/ideas/plans/{first['id']}").status_code == 200


def test_plan_includes_the_deterministic_sections_even_with_thin_prose(client, monkeypatch):
    """Evidence and settled decisions come from rows, not from the model."""
    monkeypatch.setattr(plan_mod, 'generate_spec', lambda prompt: {'summary': 'Short.'})
    idea_id = _idea(client)
    get_db().execute(
        "INSERT INTO idea_questions(id, idea_id, question, question_key, answer,"
        " status, created_at, updated_at)"
        " VALUES ('q1', ?, 'Which engine?', 'which engine', 'FTS5', 'answered', 1, 1)",
        (idea_id,),
    )
    get_db().commit()

    content = client.post(f'/api/ideas/{idea_id}/plan').get_json()['content']
    assert '## Decisions already made' in content
    assert 'FTS5' in content


def test_a_failed_generation_is_a_502_and_writes_nothing(client, monkeypatch):
    monkeypatch.setattr(plan_mod, 'generate_spec', lambda prompt: None)
    idea_id = _idea(client)
    assert client.post(f'/api/ideas/{idea_id}/plan').status_code == 502
    assert client.get(f'/api/ideas/{idea_id}/plans').get_json() == []


def test_plan_for_a_missing_idea_is_404(client):
    assert client.post('/api/ideas/nope/plan').status_code == 404


# --- Questions and list stats ---

def test_answering_a_question_settles_it_and_clearing_reopens_it(client):
    idea_id = _idea(client)
    get_db().execute(
        "INSERT INTO idea_questions(id, idea_id, question, question_key, status,"
        " created_at, updated_at) VALUES ('q1', ?, 'Which engine?', 'which engine',"
        " 'open', 1, 1)",
        (idea_id,),
    )
    get_db().commit()

    client.patch('/api/ideas/questions/q1', json={'answer': 'FTS5'})
    q = client.get(f'/api/ideas/{idea_id}/questions').get_json()[0]
    assert q['status'] == 'answered'
    assert q['answeredAt'].startswith('20')

    client.patch('/api/ideas/questions/q1', json={'answer': '  '})
    q = client.get(f'/api/ideas/{idea_id}/questions').get_json()[0]
    assert q['status'] == 'open'
    assert q['answer'] is None


def test_question_status_is_validated(client):
    assert client.patch('/api/ideas/questions/q1',
                        json={'status': 'nonsense'}).status_code == 400


def test_list_reports_the_stat_chips(client, monkeypatch):
    monkeypatch.setattr(plan_mod, 'generate_spec', lambda prompt: FULL_SPEC)
    idea_id = _idea(client)
    client.post(f'/api/ideas/{idea_id}/plan')
    get_db().execute(
        "INSERT INTO idea_questions(id, idea_id, question, question_key, status,"
        " created_at, updated_at) VALUES ('q1', ?, 'Q?', 'q', 'open', 1, 1)",
        (idea_id,),
    )
    get_db().commit()

    row = client.get('/api/ideas').get_json()[0]
    assert row['hasPlan'] is True
    assert row['openQuestionCount'] == 1
    assert row['articleCount'] == 0
    assert row['verdict'] is None, 'not assessed yet'
    assert row['assessmentStale'] is False


def test_the_user_verdict_overrides_and_is_validated(client):
    idea_id = _idea(client)
    client.patch(f'/api/ideas/{idea_id}',
                 json={'userVerdict': 'yes', 'userVerdictNote': 'I built this last week'})
    row = client.get('/api/ideas').get_json()[0]
    assert row['userVerdict'] == 'yes'
    assert client.get(f'/api/ideas/{idea_id}').get_json()['userVerdictNote'] == \
        'I built this last week'

    assert client.patch(f'/api/ideas/{idea_id}',
                        json={'userVerdict': 'sort of'}).status_code == 400
    # Clearing it hands the call back to the agent.
    client.patch(f'/api/ideas/{idea_id}', json={'userVerdict': None})
    assert client.get('/api/ideas').get_json()[0]['userVerdict'] is None


def test_assess_route_returns_evidence_and_staleness(client, monkeypatch):
    import backend.research.assess as mod
    import backend.research.repo_job as job
    monkeypatch.setattr(job, 'summarize_delta', lambda *a, **k: None)
    job.run_repo_snapshot(force=True)
    monkeypatch.setattr(mod, 'assess_idea', lambda *a, **k: {
        'verdict': 'no', 'confidence': 0.3, 'rationale': 'Not built.',
        'evidenceIndexes': [], 'openQuestions': [{'question': 'Which engine?'}],
    })

    idea_id = _idea(client)
    body = client.post(f'/api/ideas/{idea_id}/assess').get_json()
    assert body['verdict'] == 'no'
    assert body['evidence'] == []
    assert body['stale'] is False
    assert client.get(f'/api/ideas/{idea_id}/questions').get_json()[0]['question'] == 'Which engine?'

    # The list picks the assessment up.
    row = client.get('/api/ideas').get_json()[0]
    assert row['verdict'] == 'no'
    assert row['openQuestionCount'] == 1
