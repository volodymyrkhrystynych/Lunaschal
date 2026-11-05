"""APNs push: device registration, the provider token, sending and pruning."""
import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from backend.db.connection import get_db
from backend.push import apns, notify

TOKEN = 'ab' * 32


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    path = tmp_path / 'AuthKey.p8'
    path.write_bytes(pem)
    monkeypatch.setenv('APNS_KEY_PATH', str(path))
    monkeypatch.setenv('APNS_KEY_ID', 'KEY1234567')
    monkeypatch.setenv('APNS_TEAM_ID', '4AG98Q33RQ')
    monkeypatch.setenv('APNS_TOPIC', 'com.lunaschal.mobile')
    apns.reset_token_cache()
    yield key
    apns.reset_token_cache()


class FakeResp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, *statuses):
        self.statuses = list(statuses)
        self.calls = []

    def post(self, url, headers, content):
        self.calls.append((url, headers, json.loads(content)))
        status, reason = self.statuses.pop(0)
        return FakeResp(status, {'reason': reason} if reason else {})


def test_unconfigured_is_a_quiet_no_op(monkeypatch):
    for k in ('APNS_KEY_PATH', 'APNS_KEY_ID', 'APNS_TEAM_ID', 'APNS_TOPIC'):
        monkeypatch.delenv(k, raising=False)
    assert not apns.configured()
    assert notify.send_all('t', 'b', {}) == 0


def test_provider_token_is_es256_and_cached(key_file):
    config = apns.load_config()
    token = apns.provider_token(config, now=1000.0)
    header = jwt.get_unverified_header(token)
    assert header['alg'] == 'ES256' and header['kid'] == 'KEY1234567'
    claims = jwt.decode(token, key_file.public_key(), algorithms=['ES256'])
    assert claims == {'iss': '4AG98Q33RQ', 'iat': 1000}
    assert apns.provider_token(config, now=1000.0 + 60) == token
    assert apns.provider_token(config, now=1000.0 + apns.TOKEN_TTL + 1) != token


@pytest.mark.parametrize('token', ['', 'not-hex!', 'a' * 201])
def test_register_rejects_junk(client, token):
    assert client.post('/api/push/devices', json={'token': token}).status_code == 400


def test_register_is_idempotent(client, key_file):
    for env in ('sandbox', 'production'):
        resp = client.post('/api/push/devices', json={'token': TOKEN.upper(), 'environment': env})
        assert resp.get_json() == {'ok': True, 'configured': True}
    assert notify.devices() == [{'token': TOKEN, 'environment': 'production'}]


def test_send_all_sends_and_prunes_dead_tokens(client, key_file):
    notify.register_device(TOKEN, 'sandbox')
    notify.register_device('cd' * 32, 'production')
    fake = FakeClient((410, 'Unregistered'), (200, ''))
    sent = notify.send_all('Plan ready', 'body', {'taskId': 'T1'}, collapse_id='code-T1', client=fake)
    assert sent == 1
    urls = [c[0] for c in fake.calls]
    assert any(u.startswith(apns.HOSTS['sandbox']) for u in urls)
    headers = fake.calls[0][1]
    assert headers['apns-topic'] == 'com.lunaschal.mobile' and headers['apns-collapse-id'] == 'code-T1'
    assert fake.calls[0][2]['aps']['alert'] == {'title': 'Plan ready', 'body': 'body'}
    assert fake.calls[0][2]['taskId'] == 'T1'
    remaining = {r[0] for r in get_db().execute('SELECT token FROM push_devices')}
    assert len(remaining) == 1


@pytest.mark.parametrize('status, fields, expected', [
    ('awaiting_plan', {}, 'Plan ready'),
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
