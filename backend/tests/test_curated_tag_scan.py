"""The per-entry classification scan behind a new curated tag.

The behaviour worth pinning is what happens when the model *cannot answer*.
`classify_entry_for_tag` used to swallow every failure and return False, so an
unconfigured, dead or paused model produced a scan that ran to completion,
reported full progress and matched nothing — a definitive-looking empty result
for entries the model never actually saw. That is worse than an error, because
nothing about it looks wrong.
"""
import time

import pytest

from backend.ai import service
from backend.ai.journal import ClassificationUnavailable
from backend.db.connection import get_db
from backend.routes import curated_tags as ct


@pytest.fixture(autouse=True)
def _clean(client):
    ct._scan_progress.clear()
    service.reset()
    yield
    ct._scan_progress.clear()
    service.reset()


def _entries(client, n):
    for i in range(n):
        client.post('/api/journal', json={'content': f'Entry number {i}.'})


def _wait_for_scan(tag_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with ct._scan_lock:
            progress = dict(ct._scan_progress.get(tag_id) or {})
        if progress.get('done'):
            return progress
        time.sleep(0.02)
    raise AssertionError('scan never finished')


def _make_tag(client, name='cooking'):
    return client.post('/api/curated-tags', json={'name': name}).get_json()['id']


# --- backend/ai/journal.py: unknown is not "no" -----------------------------

def test_an_unconfigured_model_raises_rather_than_answering_no(monkeypatch):
    from backend.ai import journal as journal_ai
    monkeypatch.setattr(journal_ai, 'is_ai_configured', lambda: False)

    with pytest.raises(ClassificationUnavailable):
        journal_ai.classify_entry_for_tag('some content', 'cooking')


def test_a_dead_model_raises_rather_than_answering_no(monkeypatch):
    from backend.ai import journal as journal_ai
    monkeypatch.setattr(journal_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal_ai, 'chat_text', lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError('Connection refused')))

    with pytest.raises(ClassificationUnavailable, match='refused'):
        journal_ai.classify_entry_for_tag('some content', 'cooking')


def test_empty_content_is_still_a_plain_no(monkeypatch):
    """Not a model failure — there is genuinely nothing to classify."""
    from backend.ai import journal as journal_ai
    assert journal_ai.classify_entry_for_tag('   ', 'cooking') is False


def test_a_real_answer_still_comes_back(monkeypatch):
    from backend.ai import journal as journal_ai
    monkeypatch.setattr(journal_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(journal_ai, 'chat_text', lambda *a, **k: 'Yes')
    assert journal_ai.classify_entry_for_tag('a recipe', 'cooking') is True

    monkeypatch.setattr(journal_ai, 'chat_text', lambda *a, **k: 'no')
    assert journal_ai.classify_entry_for_tag('a walk', 'cooking') is False


# --- the scan loop ----------------------------------------------------------

def test_the_scan_stands_down_instead_of_marking_everything_unmatched(client, monkeypatch):
    _entries(client, 5)
    seen = []

    def _dies_after_two(content, tag_name):
        seen.append(content)
        if len(seen) > 2:
            raise ClassificationUnavailable('llama-server is down')
        return True

    monkeypatch.setattr(ct, 'classify_entry_for_tag', _dies_after_two)
    tag_id = _make_tag(client)
    progress = _wait_for_scan(tag_id)

    assert progress['stopped'], 'the scan must record why it stopped'
    # Two matched before it died; the other three are simply unjudged, not
    # judged as "no".
    matched = get_db().execute(
        'SELECT COUNT(*) AS n FROM journal_entry_curated_tags WHERE tag_id=?',
        (tag_id,)).fetchone()['n']
    assert matched == 2
    assert progress['processed'] < progress['total']


def test_a_paused_gpu_stands_the_scan_down_rather_than_emptying_the_tag(client, monkeypatch):
    _entries(client, 3)

    def _paused(content, tag_name):
        raise service.InferencePaused('GPU inference is paused')

    monkeypatch.setattr(ct, 'classify_entry_for_tag', _paused)
    tag_id = _make_tag(client)
    progress = _wait_for_scan(tag_id)

    assert progress['stopped']
    assert get_db().execute(
        'SELECT COUNT(*) AS n FROM journal_entry_curated_tags WHERE tag_id=?',
        (tag_id,)).fetchone()['n'] == 0


def test_one_bad_entry_does_not_stop_the_whole_scan(client, monkeypatch):
    """A per-entry failure is different from the model being unreachable."""
    _entries(client, 4)
    calls = []

    def _one_bad(content, tag_name):
        calls.append(content)
        if len(calls) == 2:
            raise ValueError('that entry confused the parser')
        return True

    monkeypatch.setattr(ct, 'classify_entry_for_tag', _one_bad)
    tag_id = _make_tag(client)
    progress = _wait_for_scan(tag_id)

    assert not progress.get('stopped')
    assert progress['processed'] == progress['total'] == 4
    assert get_db().execute(
        'SELECT COUNT(*) AS n FROM journal_entry_curated_tags WHERE tag_id=?',
        (tag_id,)).fetchone()['n'] == 3


def test_a_clean_scan_reports_done_with_no_stop_reason(client, monkeypatch):
    _entries(client, 3)
    monkeypatch.setattr(ct, 'classify_entry_for_tag', lambda c, t: True)

    tag_id = _make_tag(client)
    progress = _wait_for_scan(tag_id)

    assert progress['done'] is True
    assert 'stopped' not in progress
    assert progress['processed'] == 3


def test_the_scan_runs_as_background_work(client, monkeypatch):
    """One model call per journal entry with nobody waiting is the definition
    of P2 — it must yield to a chat message rather than compete with one."""
    seen = []
    monkeypatch.setattr(ct, 'classify_entry_for_tag',
                        lambda c, t: seen.append(service.current_priority()) or False)
    _entries(client, 1)

    tag_id = _make_tag(client)
    _wait_for_scan(tag_id)

    assert seen == [service.Priority.BACKGROUND]
