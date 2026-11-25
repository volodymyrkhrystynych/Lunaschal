"""What the agent is shown of new mail, what it says about it, and the limits
on what email can make it do. The model is never called."""
import json
import time

import pytest
from ulid import ULID

from backend.ai import chat as chat_ai
from backend.ai import email as email_ai
from backend.ai import jobs, service
from backend.db.connection import get_db
from backend.delegate import chat as delegate_chat
from backend.email import brief, tools
from backend.lifewiki.tools import LifeTools
from backend.push import notify
from backend.delegate.chat import wiki_tools

NOW = int(time.time())


@pytest.fixture(autouse=True)
def ready(client, monkeypatch):
    monkeypatch.setattr(brief, 'is_ai_configured', lambda: True)
    db = get_db()
    db.execute('UPDATE settings SET email_brief_since=?', (NOW - 3600,))
    db.execute("INSERT INTO email_accounts (id, provider, email_address, sync_enabled, created_at, updated_at) "
               "VALUES ('acct','gmail','me@example.com',1,1,1)")
    db.commit()


@pytest.fixture
def sends(monkeypatch):
    out = []
    monkeypatch.setattr(notify, 'send', lambda message, **kw: out.append(message) or {'pushed': 1})
    return out


def mail(subject='Interview', importance='high', labels='["INBOX"]', sender='r@acme.example',
         received=None, created=None, reason='job_next_step'):
    row_id = str(ULID())
    get_db().execute(
        'INSERT INTO emails (id, account_id, provider_message_id, subject, sender, sender_email, body_text, label_ids, '
        'received_at, created_at, importance, importance_reason, summary, category, classified_at) '
        "VALUES (?,'acct',?,?,'Acme',?,'the body',?,?,?,?,?,?,'other',1)",
        (row_id, row_id, subject, sender, labels, received or NOW, created or NOW, importance,
         reason if importance == 'high' else None, f'About {subject}'))
    get_db().commit()
    return row_id


def says(monkeypatch, answer):
    prompts = []

    def fake(text, system=None, schema=None):
        prompts.append(text)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(brief, 'chat_json', fake)
    return prompts


def posted():
    return [(r['content'], json.loads(r['metadata'])['email']) for r in get_db().execute(
        "SELECT content, metadata FROM messages WHERE metadata LIKE '%important%'")]


def briefed():
    return get_db().execute('SELECT COUNT(*) c FROM emails WHERE briefed_at IS NOT NULL').fetchone()['c']


def test_the_agent_sees_everything_and_mentions_what_it_picks(monkeypatch, sends):
    mail('Newsletter', importance='low', received=NOW - 5)
    wanted = mail('Interview')
    prompts = says(monkeypatch, {'mention': [1, 1], 'message': 'Acme wants to interview you.'})
    brief.run(NOW)
    assert 'Newsletter' in prompts[0] and 'Interview' in prompts[0]
    [(content, meta)] = posted()
    assert content == 'Acme wants to interview you.'
    assert [i['id'] for i in meta['items']] == [wanted] and meta['items'][0]['reason'] == 'job_next_step'
    assert briefed() == 2
    assert sends[0]['emailId'] == wanted and sends[0]['route'] == 'email'
    assert brief.run(NOW) is None and len(posted()) == 1


def test_two_mentions_are_one_push_to_the_inbox(monkeypatch, sends):
    mail('A'), mail('B')
    says(monkeypatch, {'mention': [1, 2], 'message': 'Two.'})
    brief.run(NOW)
    assert len(sends) == 1 and sends[0]['route'] == 'inbox' and 'emailId' not in sends[0]


def test_saying_nothing_is_an_answer(monkeypatch, sends):
    mail()
    says(monkeypatch, {'mention': [], 'message': ''})
    assert brief.run(NOW) is None
    assert posted() == [] and sends == [] and briefed() == 1


@pytest.mark.parametrize('answer', [ValueError('bad json'), {'mention': 'all'}, {'mention': [99], 'message': 'x'}, []])
def test_an_unusable_answer_falls_back_to_the_classifier(monkeypatch, sends, answer):
    mail('Interview')
    mail('Promo', importance='low')
    says(monkeypatch, answer)
    brief.run(NOW)
    [(content, meta)] = posted()
    assert [i['subject'] for i in meta['items']] == ['Interview'] and 'About Interview' in content


@pytest.mark.parametrize('exc', [service.InferencePaused('off'), service.Preempted('busy')])
def test_a_deferred_call_leaves_the_mail_for_later(monkeypatch, sends, exc):
    mail()
    says(monkeypatch, exc)
    with pytest.raises(type(exc)):
        brief.run(NOW)
    assert posted() == [] and briefed() == 0


def test_what_is_never_shown(monkeypatch):
    mail(labels='["SENT"]'), mail(labels='["SPAM"]'), mail(labels='["TRASH"]')
    mail(sender='ME@example.com')
    mail(received=NOW - 15 * 86400)
    mail(created=NOW - 7200)
    prompts = says(monkeypatch, {'mention': [], 'message': ''})
    assert brief.run(NOW) is None and prompts == []


def test_it_waits_for_the_last_classification(monkeypatch, sends):
    first = mail()
    jobs.enqueue('email.classify', first)
    prompts = says(monkeypatch, {'mention': [1], 'message': 'Hi.'})
    assert brief.run(NOW) is None and prompts == []
    get_db().execute("UPDATE llm_jobs SET status='done'")
    assert brief.run(NOW)


def test_a_long_list_is_split(monkeypatch, sends):
    for _ in range(brief.MAX_PER_PASS + 3):
        mail()
    says(monkeypatch, {'mention': [], 'message': ''})
    brief.run(NOW)
    assert briefed() == brief.MAX_PER_PASS
    assert get_db().execute("SELECT COUNT(*) c FROM llm_jobs WHERE kind='email.brief'").fetchone()['c'] == 1


def test_classifying_stores_importance_and_queues_one_brief(monkeypatch):
    a, b = mail(importance=None), mail(importance=None)
    monkeypatch.setattr(email_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(email_ai, 'chat_json', lambda *x, **k: {
        'category': 'personal', 'importance': 'high', 'reason': 'personal', 'summary': ' Ana\nasks '})
    email_ai.classify_email(a)
    email_ai.classify_email(b)
    row = get_db().execute('SELECT * FROM emails WHERE id=?', (a,)).fetchone()
    assert (row['importance'], row['importance_reason'], row['summary']) == ('high', 'personal', 'Ana asks')
    assert get_db().execute("SELECT COUNT(*) c FROM llm_jobs WHERE kind='email.brief'").fetchone()['c'] == 1


def test_a_reason_only_goes_with_high(monkeypatch):
    a = mail(importance=None)
    monkeypatch.setattr(email_ai, 'is_ai_configured', lambda: True)
    monkeypatch.setattr(email_ai, 'chat_json', lambda *x, **k: {
        'category': 'other', 'importance': 'low', 'reason': 'security', 'summary': ''})
    email_ai.classify_email(a)
    row = get_db().execute('SELECT * FROM emails WHERE id=?', (a,)).fetchone()
    assert row['importance'] == 'low' and row['importance_reason'] is None and row['summary'] is None


def test_summary_list_has_no_bodies(client):
    mail('Interview')
    mail('Promo', importance='low')
    [row] = client.get('/api/email?summary=1&importance=high').get_json()
    assert row['subject'] == 'Interview' and 'bodyText' not in row and 'bodyHtml' not in row
    assert row['importanceReason'] == 'job_next_step'


# ------------------------------------------------------------------ tools

def test_reading_mail(client):
    wanted = mail('Interview')
    box = tools.EmailTools()
    text, event = box.run_tool('list_emails', {'days': 'junk', 'importantOnly': True})
    assert wanted in text and 'the body' not in text and event['count'] == 1
    text, _ = box.run_tool('read_email', {'id': wanted})
    assert 'the body' in text and 'untrusted' in text
    assert box.run_tool('read_email', {'id': 'nope'})[1]['ok'] is False


def test_reading_mail_closes_the_web_and_the_writes_for_that_turn():
    def dispatch():
        return delegate_chat._main_dispatch(life=LifeTools(None), life_wiki=wiki_tools.WikiTools(kind='life'),
                                            checkpoint=None, deadline=None)
    run = dispatch()
    run['list_emails'].run_tool('list_emails', {})
    for name in ('delegate', 'add_todos', 'remember', 'propose_calendar_event', 'create_goal', 'record_excuse'):
        assert run[name].run_tool(name, {})[1].get('blocked') is True, name
    offered = {t['function']['name'] for t in delegate_chat.TOOLS}
    assert tools.TOOL_NAMES <= offered <= set(run)
    # A fresh turn starts open.
    assert dispatch()['add_todos'].run_tool('add_todos', {})[1].get('blocked') is None


def test_the_prompt_lists_todays_important_mail_and_a_lost_connection():
    assert chat_ai.format_email_context(NOW) == ''
    mail('Interview')
    mail('Promo', importance='low')
    get_db().execute("UPDATE email_accounts SET auth_error_at=1")
    text = chat_ai.format_email_context(NOW)
    assert 'About Interview' in text and 'Promo' not in text and 'me@example.com is disconnected' in text
