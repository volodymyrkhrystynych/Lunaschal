"""A captured idea names itself.

`title` used to stay empty forever and both the list and the server fell back
to a clipped first line — a safety net, not a name. `_enrich_idea_bg` now runs
the polish pass and then `backend/ai/idea_title.py` over its result.
"""
import pytest

from backend.ai import idea_title
from backend.routes import ideas as ideas_routes


@pytest.fixture(autouse=True)
def _sync_bg(monkeypatch):
    """Run the background job inline, so its writes can be asserted on."""
    monkeypatch.setattr(ideas_routes, 'run_bg', lambda fn: fn())


# --- backend/ai/idea_title.py ------------------------------------------------

def test_returns_empty_when_ai_unconfigured(monkeypatch):
    monkeypatch.setattr(idea_title, 'is_ai_configured', lambda: False)
    assert idea_title.generate_idea_title('a grid of habits') == ''


def test_returns_empty_on_failure(monkeypatch):
    monkeypatch.setattr(idea_title, 'is_ai_configured', lambda: True)

    def _boom(*a, **k):
        raise RuntimeError('Connection error.')
    monkeypatch.setattr(idea_title, 'chat_json', _boom)

    assert idea_title.generate_idea_title('a grid of habits') == ''


def test_returns_empty_for_empty_input(monkeypatch):
    monkeypatch.setattr(idea_title, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(idea_title, 'chat_json', lambda *a, **k: {'title': 'x'})
    assert idea_title.generate_idea_title('   ') == ''


def test_passes_a_schema_so_the_grammar_bounds_the_shape(monkeypatch):
    monkeypatch.setattr(idea_title, 'is_ai_configured', lambda: True)
    seen = {}

    def _fake(prompt, system=None, schema=None):
        seen['schema'] = schema
        return {'title': 'Habit grid in the day view'}
    monkeypatch.setattr(idea_title, 'chat_json', _fake)

    idea_title.generate_idea_title('a grid of habits')
    assert seen['schema']['required'] == ['title']


def test_passes_memory_as_context(monkeypatch):
    monkeypatch.setattr(idea_title, 'is_ai_configured', lambda: True)
    seen = {}

    def _fake(prompt, system=None, schema=None):
        seen['prompt'] = prompt
        return {'title': 'Fenwick walk tracker'}
    monkeypatch.setattr(idea_title, 'chat_json', _fake)

    idea_title.generate_idea_title('track fen wick walks', memory='The dog is named Fenwick.')
    assert 'The dog is named Fenwick.' in seen['prompt']


@pytest.mark.parametrize('raw, expected', [
    ('"Habit grid in the day view"', 'Habit grid in the day view'),
    ('Title: Habit grid', 'Habit grid'),
    ('Idea: Habit grid', 'Habit grid'),
    ('Habit grid in the day view.', 'Habit grid in the day view'),
    ('Habit grid\n\nIt would show a week at a time.', 'Habit grid'),
    ('  Habit grid  ', 'Habit grid'),
])
def test_clean_title_strips_what_models_add_anyway(raw, expected):
    assert idea_title.clean_title(raw) == expected


def test_clean_title_clips_on_a_word_boundary():
    long = 'A habit grid in the day view that also rolls up into the month ' \
           'and year summaries'
    cleaned = idea_title.clean_title(long)
    assert len(cleaned) <= idea_title.MAX_TITLE_CHARS
    # Clipped between words, not mid-word.
    assert long.startswith(cleaned)
    assert long[len(cleaned)] == ' '


def test_non_string_title_is_not_a_title(monkeypatch):
    monkeypatch.setattr(idea_title, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(idea_title, 'chat_json', lambda *a, **k: {'title': 42})
    assert idea_title.generate_idea_title('a grid of habits') == ''


# --- the route's background pass ---------------------------------------------

def _configure(monkeypatch, *, title='Habit grid in the day view', polished=None):
    monkeypatch.setattr('backend.ai.idea_title.is_ai_configured', lambda: True)
    monkeypatch.setattr(
        'backend.ai.idea_title.chat_json', lambda *a, **k: {'title': title}
    )
    monkeypatch.setattr(
        'backend.ai.idea_polish.is_ai_configured', lambda: polished is not None
    )
    if polished is not None:
        monkeypatch.setattr(
            'backend.ai.idea_polish.chat_text', lambda *a, **k: polished
        )


def test_voice_capture_names_the_idea(client, monkeypatch):
    _configure(monkeypatch, polished='A grid of habits in the day view.')

    r = client.post('/api/ideas/voice', json={'rawContent': 'grid of habits in the day view'})
    idea_id = r.get_json()['id']

    body = client.get(f'/api/ideas/{idea_id}').get_json()
    assert body['title'] == 'Habit grid in the day view'


def test_the_title_is_written_from_the_polished_text(client, monkeypatch):
    """The polish pass fixes what speech-to-text misheard; naming the idea from
    the transcript would bake the mishearing into the name."""
    seen = {}

    monkeypatch.setattr('backend.ai.idea_polish.is_ai_configured', lambda: True)
    monkeypatch.setattr(
        'backend.ai.idea_polish.chat_text', lambda *a, **k: 'A grid of habits.'
    )
    monkeypatch.setattr('backend.ai.idea_title.is_ai_configured', lambda: True)

    def _fake(prompt, system=None, schema=None):
        seen['prompt'] = prompt
        return {'title': 'Habit grid'}
    monkeypatch.setattr('backend.ai.idea_title.chat_json', _fake)

    client.post('/api/ideas/voice', json={'rawContent': 'a grid of habbits'})
    assert seen['prompt'].startswith('A grid of habits.')


def test_typed_capture_is_named_but_not_rewritten(client, monkeypatch):
    """`content` is the AI's cleanup of a transcript. What somebody typed by
    hand gets a name, not a rewrite."""
    _configure(monkeypatch, polished='SHOULD NOT BE USED')

    r = client.post('/api/ideas', json={'rawContent': 'grid of habits in the day view'})
    idea_id = r.get_json()['id']

    body = client.get(f'/api/ideas/{idea_id}').get_json()
    assert body['title'] == 'Habit grid in the day view'
    assert body['content'] == ''


def test_a_typed_title_is_never_replaced(client, monkeypatch):
    _configure(monkeypatch)
    r = client.post('/api/ideas', json={'title': 'My own name', 'rawContent': 'a grid'})
    idea_id = r.get_json()['id']
    assert client.get(f'/api/ideas/{idea_id}').get_json()['title'] == 'My own name'


def test_a_title_typed_while_the_pass_ran_wins(client, monkeypatch):
    """The pass takes tens of seconds on a local model — long enough for the
    detail pane to have saved a hand-typed name in the meantime."""
    monkeypatch.setattr('backend.ai.idea_polish.is_ai_configured', lambda: False)
    monkeypatch.setattr('backend.ai.idea_title.is_ai_configured', lambda: True)

    holder = {}

    def _fake(*a, **k):
        # Stands in for the seconds the model takes: the user renames the idea
        # while the call is in flight.
        client.patch(f"/api/ideas/{holder['id']}", json={'title': 'Typed by hand'})
        return {'title': 'Named by the model'}
    monkeypatch.setattr('backend.ai.idea_title.chat_json', _fake)

    # The background job runs inline inside the POST, so the id has to exist
    # before it fires — create the row first, then trigger the pass by hand.
    r = client.post('/api/ideas', json={'title': 'placeholder', 'rawContent': 'a grid'})
    holder['id'] = r.get_json()['id']
    client.patch(f"/api/ideas/{holder['id']}", json={'title': ''})
    ideas_routes._enrich_idea_bg(holder['id'], 'a grid', polish=False)

    body = client.get(f"/api/ideas/{holder['id']}").get_json()
    assert body['title'] == 'Typed by hand'


def test_title_stays_empty_when_ai_is_unavailable(client):
    r = client.post('/api/ideas/voice', json={'rawContent': 'grid of habits'})
    idea_id = r.get_json()['id']
    # The clipped-first-line fallback covers it, on both sides.
    assert client.get(f'/api/ideas/{idea_id}').get_json()['title'] == ''
