"""Naming an idea that was only ever dictated.

The counterpart of `displayTitle` in src/lib/ideas.ts — the two have to agree,
or the plan's heading disagrees with the list the user picked the idea from.
"""
from backend.research.idea_text import display_title


def test_an_explicit_title_wins():
    assert display_title({'title': 'Wiki UI', 'rawContent': 'something else'}) == 'Wiki UI'


def test_a_blank_title_falls_back_to_the_first_line():
    idea = {'title': '   ', 'rawContent': 'A budget tracker\nwith receipts'}
    assert display_title(idea) == 'A budget tracker'


def test_a_dictated_idea_is_clipped_on_a_word_boundary():
    idea = {'title': '', 'rawContent': (
        'A UI for the research wiki so I can read, edit, lock and revert the '
        'articles the agent writes')}
    out = display_title(idea)
    assert out == 'A UI for the research wiki so I can read, edit, lock and…'
    assert len(out) <= 61  # 60 plus the ellipsis


def test_an_unbroken_first_line_is_clipped_hard_rather_than_lost():
    out = display_title({'title': '', 'rawContent': 'x' * 100})
    assert out == 'x' * 60 + '…'


def test_snake_case_rows_work_too():
    """row_to_dict camelCases, but callers also pass raw sqlite rows."""
    assert display_title({'title': '', 'raw_content': 'From a raw row'}) == 'From a raw row'


def test_content_is_used_when_the_transcript_is_gone():
    assert display_title({'title': '', 'content': 'Polished version'}) == 'Polished version'


def test_an_empty_idea_is_untitled():
    assert display_title({'title': '', 'rawContent': '   \n  '}) == 'Untitled idea'
    assert display_title({}) == 'Untitled idea'


def test_an_idea_created_with_a_client_id_replays_once(client):
    """Same idempotent-replay contract as calendar/journal/todos: the id comes
    from the browser, so a queued offline capture can be replayed safely."""
    body = {'id': '01ARZ3NDEKTSV4RRFFQ69G5FAW', 'title': 'Offline capture'}
    assert client.post('/api/ideas', json=body).status_code == 201
    assert client.post('/api/ideas', json=body).status_code == 201

    ideas = client.get('/api/ideas').get_json()
    assert [i['title'] for i in ideas] == ['Offline capture']
