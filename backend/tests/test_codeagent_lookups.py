"""The coding agent's lookups: what it is offered, and the endpoint as the boundary."""
import json

import pytest

from backend.ai import service
from backend.codeagent import lookups as lk
from backend.codeagent import proxy
from backend.delegate import research_tools
from backend.repos import graph, registry
from backend.research import web, wiki

WEB_ON = {'web': True, 'tavily_key': 'tvly-key'}


@pytest.fixture(autouse=True)
def clean_service():
    service.reset()
    yield
    service.reset()


@pytest.fixture
def events():
    return []


@pytest.fixture
def library(monkeypatch):
    calls = []

    def run_tool(name, args):
        calls.append((name, args))
        if name == 'local_knowledge_search':
            return '1. flask docs: streaming', {'tool': name, 'ok': True, 'count': 1}
        return 'Streaming contents', {'tool': name, 'ok': True}

    monkeypatch.setattr(research_tools.knowledge_tools, 'run_tool', run_tool)
    return calls


@pytest.fixture
def delegate(monkeypatch):
    seen = []

    def run(task, **kwargs):
        seen.append({'task': task, 'priority': service.current_priority(),
                     'provider': web.search_provider()})
        return {'summary': 'Use stream_with_context.',
                'steps': [{'tool': 'web_search', 'ok': True}],
                'sources': [{'title': 'Flask docs', 'url': 'https://ex.com/f'}]}

    monkeypatch.setattr(research_tools.agent, 'run', run)
    return seen


def make(events, settings=WEB_ON, repo_id=None):
    return lk.Lookups(repo_id, lambda kind, p: events.append((kind, p)), settings=settings)


# ---------------------------------------------------------------- offering

def test_the_library_is_always_offered_and_the_web_only_with_a_key(events):
    assert make(events).names == ['library_search', 'library_read', 'web_research']
    assert make(events, {'web': True, 'tavily_key': ''}).names == ['library_search', 'library_read']
    assert make(events, {'web': False, 'tavily_key': 'k'}).names == ['library_search', 'library_read']


def test_repo_tools_appear_only_when_the_repo_has_them(events, monkeypatch, tmp_path):
    monkeypatch.setattr(registry, 'repo_root', lambda repo_id: tmp_path)
    monkeypatch.setattr(wiki, 'list_articles', lambda *a, **k: [])
    monkeypatch.setattr(graph, 'has_graph', lambda root: False)
    assert 'code_map' not in make(events, repo_id='r1').names
    assert 'repo_wiki_search' not in make(events, repo_id='r1').names

    monkeypatch.setattr(wiki, 'list_articles', lambda *a, **k: [{'slug': 'x'}])
    monkeypatch.setattr(graph, 'has_graph', lambda root: True)
    monkeypatch.setattr(graph, 'graphify_bin', lambda: '/usr/bin/graphify')
    names = make(events, repo_id='r1').names
    assert {'repo_wiki_search', 'repo_wiki_read', 'code_map'} <= set(names)


def test_offered_tools_carry_the_hosts_descriptions(events):
    [search, *_] = make(events).offered()
    assert search['name'] == 'library_search'
    assert 'offline reference library' in search['description']
    assert search['parameters']['required'] == ['queries']


# ----------------------------------------------------------------- calling

def test_a_library_search_runs_and_is_logged(events, library):
    status, result = make(events).call('library_search', {'queries': ['flask streaming', '  ']})
    assert status == 200 and result == {'text': '1. flask docs: streaming', 'ok': True}
    assert library == [('local_knowledge_search', {'queries': ['flask streaming']})]
    [(kind, ev)] = events
    assert kind == 'proxy' and ev['action'] == 'lookup' and ev['tool'] == 'library_search'
    assert ev['arg'] == 'flask streaming' and ev['count'] == 1 and ev['ok'] is True


def test_an_unoffered_name_is_refused(events):
    status, result = make(events, {'web': False, 'tavily_key': ''}).call('web_research', {'question': 'q'})
    assert status == 404 and result['ok'] is False
    assert events == []


def test_web_research_is_blocked_until_the_library_was_searched(events, library, delegate):
    lookups = make(events)
    _, result = lookups.call('web_research', {'question': 'how does flask stream?'})
    assert result['text'].startswith('Refused: search the offline library first') and result['ok'] is False
    assert 'delegate' not in result['text']
    assert delegate == []
    assert events[-1][1]['blocked'] is True
    assert lookups.web_runs == 0  # a refusal spends nothing

    lookups.call('library_search', {'queries': ['flask streaming']})
    lookups.call('library_read', {'archiveId': 'a', 'path': 'p'})
    _, result = lookups.call('web_research', {'question': 'how does flask stream?'})
    assert result == {'text': 'Use stream_with_context.', 'ok': True}
    assert events[-1][1]['sources'] == [{'title': 'Flask docs', 'url': 'https://ex.com/f'}]


def test_web_research_runs_in_the_background_through_tavily(events, delegate):
    make(events).call('web_research', {'question': 'latest llama.cpp release?', 'current': True})
    [run] = delegate
    assert run['priority'] == service.Priority.BACKGROUND
    assert run['provider'] == 'tavily'
    assert web.search_provider() != 'tavily'  # only inside the call


def test_budgets_cap_lookups_and_web_runs(events, library, delegate, monkeypatch):
    monkeypatch.setattr(lk, 'MAX_WEB', 1)
    lookups = make(events)
    lookups.call('web_research', {'question': 'q1', 'current': True})
    _, result = lookups.call('web_research', {'question': 'q2', 'current': True})
    assert 'budget' in result['text'] and len(delegate) == 1

    monkeypatch.setattr(lk, 'MAX_LOOKUPS', lookups.lookups)
    _, result = lookups.call('library_search', {'queries': ['x']})
    assert 'budget' in result['text'] and result['ok'] is False


def test_results_and_arguments_are_clipped(events, monkeypatch):
    seen = []

    def run_tool(name, args):
        seen.append(args)
        return 'x' * (lk.MAX_RESULT_CHARS + 500), {'ok': True, 'count': 1}

    monkeypatch.setattr(research_tools.knowledge_tools, 'run_tool', run_tool)
    _, result = make(events).call('library_search', {'queries': ['q' * 5000] * 9})
    assert len(result['text']) < lk.MAX_RESULT_CHARS + 100
    assert len(seen[0]['queries']) == 4 and len(seen[0]['queries'][0]) == lk.MAX_ARG_CHARS


def test_a_failing_lookup_is_a_result_not_a_crash(events, monkeypatch):
    def boom(name, args):
        raise RuntimeError('zim is corrupt')

    monkeypatch.setattr(research_tools.knowledge_tools, 'run_tool', boom)
    status, result = make(events).call('library_search', {'queries': ['x']})
    assert status == 200 and result['ok'] is False and 'zim is corrupt' in result['text']


# ------------------------------------------------------- through the proxy

def test_the_proxy_lists_and_calls_lookups(events, library, monkeypatch):
    monkeypatch.setattr(proxy, 'KEEPALIVE_SECONDS', 0.01)
    gen = proxy.Generator(lambda kind, p: None, lambda body, cancel: iter(()))
    app = proxy.make_app(gen, make(events, {'web': False, 'tavily_key': ''})).test_client()

    tools = app.get('/lunaschal/tools').get_json()['tools']
    assert [t['name'] for t in tools] == ['library_search', 'library_read']

    resp = app.post('/lunaschal/tools/library_search', json={'queries': ['flask']})
    assert resp.status_code == 200
    assert json.loads(resp.get_data(as_text=True)) == {'text': '1. flask docs: streaming', 'ok': True}

    assert app.post('/lunaschal/tools/web_research', json={}).status_code == 404


def test_a_slow_lookup_sends_whitespace_until_its_answer(events, monkeypatch):
    import time
    monkeypatch.setattr(proxy, 'KEEPALIVE_SECONDS', 0.01)

    def slow(name, args):
        time.sleep(0.08)
        return 'found it', {'ok': True, 'count': 1}

    monkeypatch.setattr(research_tools.knowledge_tools, 'run_tool', slow)
    gen = proxy.Generator(lambda kind, p: None, lambda body, cancel: iter(()))
    app = proxy.make_app(gen, make(events)).test_client()
    raw = app.post('/lunaschal/tools/library_search', json={'queries': ['x']}).get_data(as_text=True)
    assert raw.startswith(' ')
    assert json.loads(raw) == {'text': 'found it', 'ok': True}


def test_without_lookups_the_proxy_offers_nothing():
    gen = proxy.Generator(lambda kind, p: None, lambda body, cancel: iter(()))
    app = proxy.make_app(gen).test_client()
    assert app.get('/lunaschal/tools').get_json() == {'tools': []}
    assert app.post('/lunaschal/tools/library_search', json={}).status_code == 404
