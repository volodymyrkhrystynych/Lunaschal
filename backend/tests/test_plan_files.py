"""Plans that name files somebody actually opened.

A plan is handed to a coding agent that acts on it without asking anyone. A path
in it that does not exist sends that agent looking, failing, and then
improvising — so file citations get the same treatment evidence already gets:
the server builds the list, the model picks by number, and the grammar bounds
the choice during decoding.
"""
import json

import pytest

from backend.db.connection import get_db
from backend.research import plan as plan_mod
from backend.research import plan_files

pytestmark = pytest.mark.usefixtures('isolated_db')


def _repo(repo_id='r1', slug='a'):
    get_db().execute(
        'INSERT INTO repos(id, slug, name, remote_url, branch, clone_state,'
        ' is_default, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (repo_id, slug, slug.title(), f'https://h/o/{slug}.git', '', 'ready', 0, 1, 1))
    get_db().commit()
    return repo_id


def _idea(idea_id='i1', repo_id=None):
    get_db().execute(
        'INSERT INTO ideas(id, title, raw_content, content, status, repo_id,'
        ' created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
        (idea_id, 'An idea', 'raw', '', 'new', repo_id, 1, 1))
    get_db().commit()
    return idea_id


def _discussion(idea_id, sources, created=100, conversation='c1'):
    db = get_db()
    db.execute(
        'INSERT OR IGNORE INTO conversations(id, title, idea_id, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)', (conversation, 'Chat', idea_id, 1, 1))
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, metadata, created_at)'
        ' VALUES (?,?,?,?,?,?)',
        (f'm{created}', conversation, 'assistant', 'answer',
         json.dumps({'agent': 'ideas', 'sources': sources}), created))
    db.commit()


# --- Gathering ---

def test_files_read_while_discussing_this_idea_are_candidates(client):
    _idea()
    _discussion('i1', [{'file': 'backend/app.py', 'line': 12}])
    candidates = plan_files.gather_file_candidates('i1')
    assert candidates == [
        {'file': 'backend/app.py', 'why': 'read while discussing this idea'}]


def test_web_sources_are_not_files(client):
    """Pages and files share the sources list; only one of them is a path."""
    _idea()
    _discussion('i1', [{'url': 'https://ex.com/a', 'title': 'A'},
                       {'file': 'backend/app.py', 'line': 1}])
    assert [c['file'] for c in plan_files.gather_file_candidates('i1')] == \
        ['backend/app.py']


def test_another_ideas_reading_is_not_borrowed(client):
    _idea('i1')
    _idea('i2')
    _discussion('i2', [{'file': 'other/thing.py'}], conversation='c2')
    assert plan_files.gather_file_candidates('i1') == []


def test_assessment_evidence_contributes_files(client):
    _idea()
    db = get_db()
    db.execute(
        'INSERT INTO idea_assessments(id, idea_id, verdict, confidence, rationale,'
        ' evidence, assessed_at, created_at) VALUES (?,?,?,?,?,?,?,?)',
        ('a1', 'i1', 'partial', 0.5, 'some prose',
         json.dumps([{'kind': 'route', 'ref': 'GET /x', 'file': 'backend/routes/x.py'}]),
         1, 1))
    db.execute("UPDATE ideas SET assessment_id='a1' WHERE id='i1'")
    db.commit()

    assert plan_files.gather_file_candidates('i1') == [
        {'file': 'backend/routes/x.py', 'why': 'cited as existing machinery'}]


def test_module_notes_contribute_the_files_the_nightly_pass_read(client):
    _repo()
    _idea('i1', repo_id='r1')
    db = get_db()
    db.execute(
        'INSERT INTO wiki_articles(id, repo_id, slug, title, kind, sources,'
        ' created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
        ('w1', 'r1', 'backend', 'Backend', 'code',
         json.dumps([{'file': 'backend/worker.py', 'line': 3}]), 1, 1))
    db.commit()

    assert plan_files.gather_file_candidates('i1', 'r1') == [
        {'file': 'backend/worker.py', 'why': 'covered by a module note'}]


def test_discussion_reading_outranks_the_other_two_sources(client):
    """It was opened while thinking about *this* idea, so it is the most likely
    to be where the work goes."""
    _repo()
    _idea('i1', repo_id='r1')
    db = get_db()
    db.execute(
        'INSERT INTO wiki_articles(id, repo_id, slug, title, kind, sources,'
        ' created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
        ('w1', 'r1', 'm', 'M', 'code', json.dumps([{'file': 'from/wiki.py'}]), 1, 1))
    db.commit()
    _discussion('i1', [{'file': 'from/discussion.py'}])

    assert [c['file'] for c in plan_files.gather_file_candidates('i1', 'r1')] == \
        ['from/discussion.py', 'from/wiki.py']


def test_a_file_seen_twice_is_listed_once_with_its_strongest_reason(client):
    _repo()
    _idea('i1', repo_id='r1')
    db = get_db()
    db.execute(
        'INSERT INTO wiki_articles(id, repo_id, slug, title, kind, sources,'
        ' created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
        ('w1', 'r1', 'm', 'M', 'code', json.dumps([{'file': 'shared.py'}]), 1, 1))
    db.commit()
    _discussion('i1', [{'file': 'shared.py'}])

    candidates = plan_files.gather_file_candidates('i1', 'r1')
    assert candidates == [
        {'file': 'shared.py', 'why': 'read while discussing this idea'}]


def test_nothing_read_means_no_candidates_rather_than_a_guess(client):
    _idea()
    assert plan_files.gather_file_candidates('i1') == []


def test_malformed_metadata_is_skipped_not_fatal(client):
    _idea()
    db = get_db()
    db.execute(
        'INSERT INTO conversations(id, title, idea_id, created_at, updated_at)'
        " VALUES ('c1','Chat','i1',1,1)")
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, metadata, created_at)'
        " VALUES ('m1','c1','assistant','a','{not json',1)")
    db.commit()
    assert plan_files.gather_file_candidates('i1') == []


def test_the_candidate_list_is_bounded(client, monkeypatch):
    monkeypatch.setattr(plan_files, 'MAX_CANDIDATES', 3)
    _idea()
    _discussion('i1', [{'file': f'f{n}.py'} for n in range(10)])
    assert len(plan_files.gather_file_candidates('i1')) == 3


# --- Resolving ---

def test_indexes_resolve_to_real_paths(client):
    candidates = [{'file': 'a.py', 'why': 'x'}, {'file': 'b.py', 'why': 'y'},
                  {'file': 'c.py', 'why': 'z'}]
    assert plan_files.resolve_indexes([1, 3], candidates) == [
        {'file': 'a.py', 'why': 'x'}, {'file': 'c.py', 'why': 'z'}]


@pytest.mark.parametrize('indexes', [[0], [4], [-1], ['two'], [None], []])
def test_an_unusable_index_is_dropped_rather_than_raising(client, indexes):
    """The grammar bounds these during decoding so this should be impossible.
    Dropping beats raising anyway: the one thing worse than a plan missing a
    file is no plan at all."""
    assert plan_files.resolve_indexes(indexes, [{'file': 'a.py', 'why': 'x'}]) == []


def test_the_same_index_twice_yields_one_entry(client):
    candidates = [{'file': 'a.py', 'why': 'x'}]
    assert len(plan_files.resolve_indexes([1, 1], candidates)) == 1


def test_the_rendered_list_is_one_based_like_the_evidence_list(client):
    text = plan_files.render_candidates(
        [{'file': 'a.py', 'why': 'read'}, {'file': 'b.py', 'why': 'cited'}])
    assert text == '1. a.py (read)\n2. b.py (cited)'


# --- The schema ---

def test_the_schema_bounds_the_indexes_to_the_candidate_count(client):
    schema = plan_mod.build_schema(4)
    items = schema['properties']['fileIndexes']['items']
    assert items['minimum'] == 1 and items['maximum'] == 4


def test_with_no_candidates_the_field_is_absent_not_empty(client):
    """The prompt does not mention it either, so the model is never asked to
    pick from nothing."""
    assert 'fileIndexes' not in plan_mod.build_schema(0)['properties']


def test_building_a_schema_does_not_mutate_the_template(client):
    plan_mod.build_schema(3)
    assert 'fileIndexes' not in plan_mod._SCHEMA['properties']


# --- Rendering ---

def test_the_plan_renders_a_files_section(client):
    out = plan_mod.render_plan_markdown(
        'An idea', {'summary': 'Do it.'},
        files=[{'file': 'backend/app.py', 'why': 'read while discussing this idea'}])
    assert '## Files to start from' in out
    assert '- `backend/app.py` — read while discussing this idea' in out


def test_no_files_means_no_section_rather_than_an_empty_one(client):
    out = plan_mod.render_plan_markdown('An idea', {'summary': 'Do it.'}, files=[])
    assert 'Files to start from' not in out


def test_a_file_with_no_reason_does_not_render_a_dangling_dash(client):
    out = plan_mod.render_plan_markdown(
        'An idea', {'summary': 'x'}, files=[{'file': 'a.py'}])
    assert '- `a.py`\n' in out


# --- End to end through the route ---

def test_the_plan_route_bounds_and_resolves_the_model_choice(client, monkeypatch):
    import backend.routes.ideas as routes
    monkeypatch.setattr(routes, 'is_ai_configured', lambda: True)
    _idea()
    _discussion('i1', [{'file': 'backend/app.py', 'line': 4},
                       {'file': 'src/App.tsx', 'line': 1}])

    seen = {}

    def fake_generate(prompt, schema=None):
        seen['prompt'] = prompt
        seen['schema'] = schema
        # Index 2 of the two candidates.
        return {'summary': 'Do it.', 'fileIndexes': [2]}

    monkeypatch.setattr('backend.research.plan.generate_spec', fake_generate)

    resp = client.post('/api/ideas/i1/plan')
    assert resp.status_code == 201

    # The candidate list reached the prompt, numbered.
    assert '1. backend/app.py' in seen['prompt']
    assert '2. src/App.tsx' in seen['prompt']
    # And the grammar was bounded to it.
    assert seen['schema']['properties']['fileIndexes']['items']['maximum'] == 2

    content = resp.get_json()['content']
    assert '## Files to start from' in content
    assert '`src/App.tsx`' in content
    assert '`backend/app.py`' not in content.split('## Files to start from')[1]


def test_an_idea_nobody_has_read_for_still_produces_a_plan(client, monkeypatch):
    import backend.routes.ideas as routes
    monkeypatch.setattr(routes, 'is_ai_configured', lambda: True)
    _idea()

    seen = {}

    def fake_generate(prompt, schema=None):
        seen['schema'] = schema
        seen['prompt'] = prompt
        return {'summary': 'Do it.'}

    monkeypatch.setattr('backend.research.plan.generate_spec', fake_generate)

    resp = client.post('/api/ideas/i1/plan')
    assert resp.status_code == 201
    assert 'fileIndexes' not in seen['schema']['properties']
    assert 'Candidate files' not in seen['prompt']
    assert 'Files to start from' not in resp.get_json()['content']
