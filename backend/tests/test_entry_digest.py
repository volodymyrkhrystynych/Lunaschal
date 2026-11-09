"""The nightly per-entry description: what it is built from, when it is
(re)written, and how the briefing and the life wiki read it.

The point of the feature is an entry made of screenshots: before it, the
briefing saw a title over an empty body. So most of these build one.
"""
import time

import pytest
from ulid import ULID

from backend.ai import entry_digest
from backend.ai.service import InferencePaused, Preempted
from backend.db.connection import get_db
from backend.journal import digest_job

NOW = int(time.time())


@pytest.fixture(autouse=True)
def ai_on(monkeypatch):
    monkeypatch.setattr('backend.ai.provider.is_ai_configured', lambda: True)


def _entry(content='', title=None, when=None, entry_id=None):
    db = get_db()
    entry_id = entry_id or str(ULID())
    when = when or NOW - 3600
    db.execute(
        'INSERT INTO journal_entries(id, content, raw_content, title, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?)',
        (entry_id, content, content, title, when, when),
    )
    db.commit()
    return entry_id


def _attach(entry_id, kind='image', name='2026-11-07 14:02:11', transcript=None,
            description=None, screenshot=False, position=0,
            transcript_status='done', description_status='idle', import_status='ready'):
    db = get_db()
    aid = str(ULID())
    db.execute(
        'INSERT INTO journal_attachments(id, entry_id, kind, name, path, position,'
        ' transcript, transcript_status, description, description_status,'
        ' import_status, is_screenshot, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (aid, entry_id, kind, name, f'/x/{aid}', position, transcript,
         transcript_status, description, description_status, import_status,
         1 if screenshot else 0, NOW - 3600),
    )
    db.commit()
    return aid


def _row(entry_id):
    return dict(get_db().execute(
        'SELECT digest, digest_inputs_hash, digested_at, updated_at'
        ' FROM journal_entries WHERE id=?', (entry_id,)).fetchone())


@pytest.fixture
def model(monkeypatch):
    """A describer that records what it was handed."""
    calls = []

    def describe(entry):
        calls.append({'entry': entry})
        return f'Described {entry["id"]}'

    monkeypatch.setattr(entry_digest, 'describe_entry', describe)
    return calls


# --- the prompt ------------------------------------------------------------


def test_the_prompt_carries_every_attachment_reading_and_labels_screenshots():
    prompt = entry_digest.build_prompt({
        'title': 'Screenshots', 'body': '', 'created_at': NOW,
        'attachments': [
            {'kind': 'image', 'name': '2026-11-07 14:02:11', 'is_screenshot': 1,
             'transcript': 'Amazon product page: "Flexispot E7", $649.',
             'description': None, 'created_at': NOW},
            {'kind': 'image', 'name': 'the leak', 'is_screenshot': 0,
             'transcript': 'A wet pipe under a sink.', 'description': None,
             'created_at': NOW},
            {'kind': 'audio', 'name': 'Recording', 'transcript': 'Order the desk.',
             'description': 'Keyboard typing.', 'created_at': NOW},
            {'kind': 'image', 'name': 'unread', 'is_screenshot': 1,
             'transcript': None, 'description': None, 'created_at': NOW},
        ],
    })
    assert 'Flexispot E7' in prompt and 'Screenshot,' in prompt
    assert 'Photo "the leak"' in prompt and 'A wet pipe under a sink.' in prompt
    assert 'What was said: Order the desk.' in prompt
    assert 'Sounds: Keyboard typing.' in prompt
    assert '(not read)' in prompt
    assert 'only its attachments' in prompt
    assert 'Attachments (4, in order)' in prompt


def test_the_prompt_clips_a_long_transcript():
    prompt = entry_digest.build_prompt({
        'title': None, 'body': 'Notes.', 'created_at': NOW,
        'attachments': [{'kind': 'audio', 'name': 'Talk', 'description': None,
                         'transcript': 'word ' * 5000, 'created_at': NOW}],
    })
    assert '[…]' in prompt
    assert len(prompt) < entry_digest.MEDIA_TEXT_MAX_CHARS + 1000


def test_describe_entry_asks_for_the_schema_and_leaves_thinking_to_the_thread(monkeypatch):
    captured = {}

    def fake_chat_json(prompt, system=None, schema=None, **kw):
        captured.update(kw, schema=schema, system=system)
        return {'description': '  They saved a desk listing.  '}

    monkeypatch.setattr(entry_digest, 'chat_json', fake_chat_json)
    text = entry_digest.describe_entry(
        {'title': None, 'body': 'x', 'created_at': NOW, 'attachments': []})
    assert text == 'They saved a desk listing.'
    assert captured['schema'] is entry_digest.SCHEMA
    assert captured['max_tokens'] == entry_digest.DIGEST_MAX_TOKENS
    assert 'thinking' not in captured


def test_an_empty_answer_is_none(monkeypatch):
    monkeypatch.setattr(entry_digest, 'chat_json', lambda *a, **k: {'description': '  '})
    assert entry_digest.describe_entry(
        {'title': None, 'body': 'x', 'created_at': NOW, 'attachments': []}) is None


# --- which entries, and when ---------------------------------------------


def test_a_screenshot_entry_gets_a_description_and_updated_at_is_left_alone(client, model):
    entry = _entry(title='Screenshots')
    _attach(entry, screenshot=True, transcript='A chat in Discord about desks.')
    before = _row(entry)['updated_at']

    result = digest_job.run_entry_digest_pass()

    assert result['written'] == 1
    row = _row(entry)
    assert row['digest'] == f'Described {entry}'
    assert row['digest_inputs_hash'] and row['digested_at']
    # Derived, not an edit: mobile sync keys on updated_at.
    assert row['updated_at'] == before
    seen = model[0]['entry']
    assert seen['attachments'][0]['transcript'] == 'A chat in Discord about desks.'


def test_a_short_text_entry_is_its_own_description(client, model):
    _entry('Slept well, gym at 6.')
    assert digest_job.run_entry_digest_pass()['written'] == 0
    assert model == []


def test_a_long_text_entry_gets_one(client, model):
    entry = _entry('word ' * 600)
    digest_job.run_entry_digest_pass()
    assert _row(entry)['digest'] == f'Described {entry}'


def test_an_unchanged_entry_is_not_described_twice(client, model):
    entry = _entry()
    _attach(entry, transcript='A sunset.')
    digest_job.run_entry_digest_pass()
    digest_job.run_entry_digest_pass()
    assert len(model) == 1


def test_a_caption_that_lands_later_redoes_the_description(client, model):
    entry = _entry('Look at this.')
    aid = _attach(entry, transcript=None, transcript_status='idle')
    digest_job.run_entry_digest_pass()
    get_db().execute("UPDATE journal_attachments SET transcript='A heron.' WHERE id=?", (aid,))
    get_db().commit()

    digest_job.run_entry_digest_pass()
    assert len(model) == 2
    assert model[1]['entry']['attachments'][0]['transcript'] == 'A heron.'


def test_an_entry_still_being_read_waits_for_the_next_night(client, model):
    entry = _entry()
    _attach(entry, transcript=None, transcript_status='running')
    result = digest_job.run_entry_digest_pass()
    assert result['unsettled'] == 1 and model == []
    assert _row(entry)['digest'] is None


def test_a_page_queued_for_the_browser_does_not_hold_the_entry_back(client, model):
    entry = _entry('Read this later.')
    _attach(entry, kind='webpage', name='An article', transcript=None,
            transcript_status='idle', import_status='queued')
    assert digest_job.run_entry_digest_pass()['written'] == 1
    assert _row(entry)['digest']


def test_a_description_of_attachments_that_are_gone_is_cleared(client, model):
    entry = _entry('Short.')
    aid = _attach(entry, transcript='A cat.')
    digest_job.run_entry_digest_pass()
    get_db().execute('DELETE FROM journal_attachments WHERE id=?', (aid,))
    get_db().commit()

    result = digest_job.run_entry_digest_pass()
    assert result['cleared'] == 1
    assert _row(entry)['digest'] is None and _row(entry)['digest_inputs_hash'] is None


def test_entries_outside_the_window_are_left_alone(client, model):
    old = _entry(when=NOW - (digest_job.WINDOW_DAYS + 1) * 86400)
    _attach(old, transcript='Old photo.')
    assert digest_job.run_entry_digest_pass()['written'] == 0


def test_newest_first_so_the_deadline_spends_itself_on_what_the_briefing_reads(
        client, model, monkeypatch):
    older = _entry(when=NOW - 7200)
    _attach(older, transcript='Older.')
    newer = _entry(when=NOW - 600)
    _attach(newer, transcript='Newer.')
    ticks = iter([0.0, 10.0])
    monkeypatch.setattr(digest_job.time, 'monotonic', lambda: next(ticks, 99.0))

    result = digest_job.run_entry_digest_pass(deadline=5.0)
    assert result['timedOut'] is True
    assert _row(newer)['digest'] and _row(older)['digest'] is None


# --- failures ---------------------------------------------------------------


def test_a_paused_gpu_stops_the_pass_and_writes_nothing(client, monkeypatch):
    for _ in range(2):
        _attach(_entry(), transcript='x')
    calls = []

    def paused(entry):
        calls.append(1)
        raise InferencePaused('paused')

    monkeypatch.setattr(entry_digest, 'describe_entry', paused)
    result = digest_job.run_entry_digest_pass()
    assert result['paused'] is True and len(calls) == 1
    assert get_db().execute(
        'SELECT COUNT(*) FROM journal_entries WHERE digest IS NOT NULL').fetchone()[0] == 0


def test_a_preempted_call_is_retried(client, monkeypatch):
    entry = _entry()
    _attach(entry, transcript='x')
    attempts = []

    def flaky(e):
        attempts.append(1)
        if len(attempts) == 1:
            raise Preempted('a chat message took the lane')
        return 'Second time lucky.'

    monkeypatch.setattr(entry_digest, 'describe_entry', flaky)
    assert digest_job.run_entry_digest_pass()['written'] == 1
    assert _row(entry)['digest'] == 'Second time lucky.'


def test_one_failed_entry_does_not_cost_the_others(client, monkeypatch):
    bad = _entry(when=NOW - 600)
    _attach(bad, transcript='x')
    good = _entry(when=NOW - 7200)
    _attach(good, transcript='y')

    def describe(entry):
        if entry['id'] == bad:
            raise RuntimeError('model fell over')
        return 'Fine.'

    monkeypatch.setattr(entry_digest, 'describe_entry', describe)
    result = digest_job.run_entry_digest_pass()
    assert result == {**result, 'written': 1, 'failed': 1}
    assert _row(good)['digest'] == 'Fine.'
    # No hash stored, so tomorrow tries it again.
    assert _row(bad)['digest_inputs_hash'] is None


# --- the readers --------------------------------------------------------------


def test_the_briefing_reads_the_description_in_place_of_an_empty_body(client):
    from backend.ai.briefing import build_briefing_prompt, gather_briefing_context

    entry = _entry(title='Screenshots')
    get_db().execute(
        "UPDATE journal_entries SET digest='They compared two standing desks.' WHERE id=?",
        (entry,))
    get_db().commit()

    prompt = build_briefing_prompt(gather_briefing_context())
    assert 'Screenshots (described, with its attachments)' in prompt
    assert 'They compared two standing desks.' in prompt


def test_the_chat_prompt_keeps_the_body(client):
    from backend.ai.chat import format_journal_context

    entries = [{'title': 'T', 'content': 'Own words.', 'digest': 'Described.',
                'created_at': NOW}]
    assert 'Own words.' in format_journal_context(entries, NOW)
    assert 'Described.' not in format_journal_context(entries, NOW)
    assert 'Described.' in format_journal_context(entries, NOW, use_digest=True)


def test_the_life_wiki_sees_it_labelled_as_not_their_words(client):
    from backend.day_boundary import day_key_for
    from backend.lifewiki import digest as wiki_digest

    entry = _entry('Desk shopping.', when=NOW - 60)
    get_db().execute(
        "UPDATE journal_entries SET digest='They compared the Flexispot E7.' WHERE id=?",
        (entry,))
    get_db().commit()

    lines = wiki_digest.gather(day_key_for(NOW), 2)['sources']['journal']
    line = next(l for l in lines if entry in l)
    assert 'Desk shopping.' in line
    assert 'not their words' in line and 'Flexispot E7' in line
