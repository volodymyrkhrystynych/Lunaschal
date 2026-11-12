"""APNs delivery (backend/push/, backend/coach/deliver.py, backend/routes/push.py)
for both of its senders, the coach and the Code tab.

Apple is never contacted: the HTTP client is an httpx.MockTransport, and the
signing key is generated per test.
"""
import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from backend.coach import deliver
from backend.db.connection import _ensure_push_device_columns, get_db
from backend.push import apns, notify, payload

TOKEN = 'ab' * 32


@pytest.fixture
def key(tmp_path, monkeypatch):
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    path = tmp_path / 'AuthKey.p8'
    path.write_text(pem)
    monkeypatch.setenv('APNS_KEY_PATH', str(path))
    monkeypatch.setenv('APNS_KEY_ID', 'KEY123')
    monkeypatch.delenv('APNS_TEAM_ID', raising=False)
    monkeypatch.delenv('APNS_TOPIC', raising=False)
    return private.public_key()


class Apple:
    """Answers each send with the next queued (status, reason)."""

    def __init__(self, *answers):
        self.answers = list(answers) or [(200, None)]
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        status, reason = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return httpx.Response(status, json={'reason': reason} if reason else None)

    def client(self, clock=lambda: 1_000_000.0):
        return apns.Client(apns.config(), http=httpx.Client(transport=httpx.MockTransport(self)), clock=clock)


def device(token=TOKEN, environment='production', enabled=1, coach=1):
    get_db().execute('INSERT INTO push_devices (id,token,environment,enabled,coach,last_seen_at,created_at,updated_at) '
                     'VALUES (?,?,?,?,?,1,1,1)', (token[:26], token, environment, enabled, coach))
    get_db().commit()


def test_unconfigured_is_none(monkeypatch):
    monkeypatch.delenv('APNS_KEY_PATH', raising=False)
    assert apns.config() is None and apns.client() is None


def test_request_shape_and_signed_token(key):
    apple = Apple()
    result = apple.client().send(TOKEN, 'sandbox', payload.alert('T', 'B', category='C'), collapse_id='x' * 80)
    assert result.ok
    [req] = apple.requests
    assert str(req.url) == f'https://api.sandbox.push.apple.com/3/device/{TOKEN}'
    assert req.headers['apns-topic'] == 'com.lunaschal.mobile'
    assert req.headers['apns-push-type'] == 'alert'
    assert len(req.headers['apns-collapse-id']) == 64
    token = req.headers['authorization'].removeprefix('bearer ')
    assert jwt.get_unverified_header(token)['kid'] == 'KEY123'
    claims = jwt.decode(token, key, algorithms=['ES256'])
    assert claims['iss'] == '4AG98Q33RQ'
    body = json.loads(req.content)
    assert body['aps']['alert'] == {'title': 'T', 'body': 'B'} and body['aps']['category'] == 'C'


def test_tokens_are_reused_and_refreshed_only_when_old_enough(key):
    now = [1_000_000.0]
    tokens = apns.ProviderToken(apns.config(), clock=lambda: now[0])
    first = tokens.get()
    now[0] += 60
    assert tokens.get() == first
    assert tokens.get(expired=True) == first  # under 20 minutes: Apple would refuse a new one
    now[0] += apns.TOKEN_MIN_AGE
    second = tokens.get(expired=True)
    assert second != first
    now[0] += apns.TOKEN_REUSE
    assert tokens.get() != second


def test_an_expired_token_is_refreshed_and_retried_once(key):
    now = [1_000_000.0]
    apple = Apple((403, 'ExpiredProviderToken'), (200, None))
    client = apple.client(clock=lambda: now[0])
    client.tokens.get()
    now[0] += apns.TOKEN_MIN_AGE + 1
    assert client.send(TOKEN, 'production', payload.alert('T', 'B', category='C')).ok
    auths = [r.headers['authorization'] for r in apple.requests]
    assert len(auths) == 2 and auths[0] != auths[1]


def test_payload_is_cut_to_four_kilobytes():
    big = payload.alert('T', 'é' * 5000, category='C', data={'kind': 'x'})
    assert len(payload.encode(big)) <= payload.MAX_BYTES
    assert big['aps']['alert']['body'].endswith('…')
    small = payload.alert('T', 'Short', category='C')
    assert small['aps']['alert']['body'] == 'Short'


def test_deliver_saves_the_message_even_without_apns(monkeypatch):
    monkeypatch.delenv('APNS_KEY_PATH', raising=False)
    device()
    sent = deliver.deliver('Hello', {'coach': {'kind': 'checkin', 'day': '2026-11-03'}},
                           title='T', body='B', category='COACH_CHECKIN', now=1_800_000_000)
    assert sent['pushed'] == 0 and sent['configured'] is False
    assert get_db().execute('SELECT content FROM messages WHERE id=?', (sent['messageId'],)).fetchone()[0] == 'Hello'


def test_deliver_pushes_and_dead_tokens_are_disabled(key):
    device()
    device('cd' * 32, 'sandbox')
    apple = Apple((200, None), (410, 'Unregistered'))
    sent = deliver.deliver('Hi', {'coach': {'kind': 'checkin', 'day': 'd'}}, title='T', body='B',
                           category='COACH_CHECKIN', client=apple.client(), now=1_800_000_000)
    assert sent['pushed'] == 1 and sent['errors'] == ['410 Unregistered']
    data = json.loads(apple.requests[0].content)['coach']
    assert data['messageId'] == sent['messageId'] and data['kind'] == 'checkin'
    rows = {r['token']: r for r in get_db().execute('SELECT * FROM push_devices')}
    assert rows[TOKEN]['enabled'] == 1 and rows[TOKEN]['last_sent_at']
    assert rows['cd' * 32]['enabled'] == 0 and rows['cd' * 32]['last_error'] == '410 Unregistered'


def test_a_server_error_keeps_the_device(key):
    device()
    deliver.push('T', 'B', category='C', data={}, client=Apple((500, 'InternalServerError')).client())
    assert get_db().execute('SELECT enabled FROM push_devices').fetchone()[0] == 1


def test_device_routes_never_return_the_token_or_key(client, key):
    assert client.post('/api/push/devices', json={'token': 'nope', 'environment': 'sandbox'}).status_code == 400
    assert client.post('/api/push/devices', json={'token': TOKEN, 'environment': 'staging'}).status_code == 400
    assert client.post('/api/push/devices', json={'token': TOKEN.upper(), 'environment': 'sandbox',
                                                  'appVersion': '1.0'}).status_code == 200
    get_db().execute('UPDATE push_devices SET enabled=0')
    get_db().commit()
    client.post('/api/push/devices', json={'token': TOKEN, 'environment': 'production'})
    status = client.get('/api/push/status').json
    assert status['configured'] is True
    [d] = status['devices']
    assert d['enabled'] == 1 and d['environment'] == 'production' and d['token'] == '…ababab'
    text = json.dumps(status)
    assert TOKEN not in text and 'AuthKey' not in text and 'BEGIN' not in text
    assert client.delete(f'/api/push/devices/{TOKEN}').status_code == 200
    assert client.get('/api/push/status').json['devices'] == []


@pytest.mark.parametrize('token', ['', 'not-hex!', 'a' * 201, 'abc', 12345])
def test_register_rejects_junk(client, token):
    assert client.post('/api/push/devices', json={'token': token}).status_code == 400


def test_register_is_idempotent_and_says_whether_the_server_can_send(client, key):
    for env in ('sandbox', 'production'):
        r = client.post('/api/push/devices', json={'token': TOKEN.upper(), 'environment': env})
        assert r.get_json() == {'ok': True, 'configured': True}
    assert notify.devices() == [{'token': TOKEN, 'environment': 'production'}]


def test_opening_the_code_page_does_not_turn_reminders_on_or_off(client):
    coach = lambda: get_db().execute('SELECT coach FROM push_devices').fetchone()[0]
    client.post('/api/push/devices', json={'token': TOKEN, 'environment': 'production', 'platform': 'ios'})
    assert coach() == 0
    client.post('/api/push/devices', json={'token': TOKEN, 'environment': 'production', 'coach': True})
    assert coach() == 1
    client.post('/api/push/devices', json={'token': TOKEN, 'environment': 'production', 'platform': 'ios'})
    assert coach() == 1
    client.post('/api/push/devices', json={'token': TOKEN, 'environment': 'production', 'coach': False})
    assert coach() == 0


def test_a_code_registration_keeps_what_the_coach_registration_recorded(client):
    client.post('/api/push/devices', json={'token': TOKEN, 'environment': 'production', 'coach': True,
                                           'bundleId': 'com.lunaschal.mobile', 'appVersion': '1'})
    client.post('/api/push/devices', json={'token': TOKEN, 'environment': 'production'})
    row = get_db().execute('SELECT bundle_id, app_version FROM push_devices').fetchone()
    assert tuple(row) == ('com.lunaschal.mobile', '1')


def test_coach_pushes_only_reach_phones_that_turned_reminders_on(key):
    device(coach=1)
    device('cd' * 32, coach=0)
    apple = Apple()
    sent = deliver.push('T', 'B', category='C', data={}, client=apple.client())
    assert sent['pushed'] == 1
    assert [r.url.path.rsplit('/', 1)[1] for r in apple.requests] == [TOKEN]


def test_code_notifications_reach_every_enabled_device(key):
    device(coach=1)
    device('cd' * 32, coach=0)
    device('ef' * 32, enabled=0)
    apple = Apple()
    assert notify.send_all('Plan ready', 'body', {'taskId': 'T1'}, client=apple.client()) == 2


def test_code_payload_keeps_its_fields_beside_aps(key):
    device()
    apple = Apple()
    notify.send_all('Plan ready', 'body', {'kind': 'code_task', 'taskId': 'T1', 'status': 'done'},
                    collapse_id='code-T1', client=apple.client())
    [req] = apple.requests
    assert req.headers['apns-collapse-id'] == 'code-T1'
    body = json.loads(req.content)
    assert body['taskId'] == 'T1' and body['kind'] == 'code_task' and 'coach' not in body
    assert body['aps']['thread-id'] == 'T1' and 'category' not in body['aps']


def test_unconfigured_send_all_is_a_quiet_no_op(monkeypatch):
    monkeypatch.delenv('APNS_KEY_PATH', raising=False)
    assert not apns.configured()
    assert notify.send_all('t', 'b', {}) == 0


def test_a_device_table_from_the_code_tab_gains_the_coach_columns():
    db = get_db()
    db.execute('DROP TABLE push_devices')
    db.execute("""CREATE TABLE push_devices (
        id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE,
        environment TEXT NOT NULL DEFAULT 'production' CHECK(environment IN ('sandbox','production')),
        platform TEXT NOT NULL DEFAULT 'ios', created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL)""")
    db.execute("INSERT INTO push_devices (id,token,created_at,last_seen_at) VALUES ('d1',?,1,1)", (TOKEN,))
    _ensure_push_device_columns(db)
    _ensure_push_device_columns(db)
    row = db.execute('SELECT enabled, coach, updated_at FROM push_devices').fetchone()
    assert tuple(row) == (1, 0, 0)
    notify.register_device(TOKEN, 'sandbox', coach=True)
    assert db.execute('SELECT coach FROM push_devices').fetchone()[0] == 1


@pytest.mark.parametrize('status, fields, expected', [
    ('awaiting_plan', {}, 'Plan ready'),
    ('answered', {}, 'answered “Fix it”'),
    ('done', {'testExit': 0}, 'tests passed'),
    ('done', {'testExit': 2}, 'tests failed'),
    ('needs_input', {'pending': {'request': {'question': 'Which file?'}}}, 'Which file?'),
    ('error', {'error': 'boom'}, 'boom'),
])
def test_messages(status, fields, expected):
    title, body = notify.message_for({'title': 'Fix it', **fields}, status)
    assert expected in title + body


def test_no_message_for_quiet_states():
    assert notify.message_for({'title': 'x'}, 'running') is None


def test_test_route_needs_configuration(client, monkeypatch):
    monkeypatch.delenv('APNS_KEY_PATH', raising=False)
    assert client.post('/api/push/test').status_code == 409


def test_a_notification_reply_becomes_a_message_the_agent_answers_once(client, monkeypatch):
    from backend.chat import autoreply
    started = []
    monkeypatch.setattr(autoreply, 'start_reply', lambda conv, msg: started.append((conv, msg)) or 'r1')
    r = client.post('/api/coach/reply', json={'id': '01REPLY', 'text': 'Busy day, skipped the gym'})
    assert r.status_code == 201 and r.json['replyId'] == 'r1'
    again = client.post('/api/coach/reply', json={'id': '01REPLY', 'text': 'Busy day, skipped the gym'})
    assert again.json['replyId'] is None
    assert len(started) == 1
    assert client.post('/api/coach/reply', json={'id': '01X', 'text': ''}).status_code == 400
