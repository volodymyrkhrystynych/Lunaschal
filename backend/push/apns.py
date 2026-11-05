"""Apple Push Notification service: token-based auth over HTTP/2.

Configuration is deploy-time, not a Settings field, and lives in the
environment (.env in the repo root, which ops/run-prod.sh loads at start):

    APNS_KEY_PATH   the .p8 auth key downloaded from the developer portal
    APNS_KEY_ID     its 10-character key id
    APNS_TEAM_ID    the team id (4AG98Q33RQ)
    APNS_TOPIC      the app's bundle id

With any of them missing, `configured()` is False and nothing is sent — the
Code tab still works, it just cannot call you back.

The provider token is an ES256 JWT that Apple accepts for up to an hour and
rejects if refreshed more than once every twenty minutes, so it is cached for
forty. A device token Apple answers 410 (Unregistered) or 400
(BadDeviceToken) for is reported back so the caller can delete its row.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass

import jwt

logger = logging.getLogger(__name__)

HOSTS = {'production': 'https://api.push.apple.com',
         'sandbox': 'https://api.sandbox.push.apple.com'}
TOKEN_TTL = 40 * 60
TIMEOUT = 15

_token_lock = threading.Lock()
_token_cache: tuple[str, float] | None = None


@dataclass(frozen=True)
class Config:
    key: str
    key_id: str
    team_id: str
    topic: str


@dataclass
class Result:
    ok: bool
    status: int
    reason: str = ''

    @property
    def token_is_dead(self) -> bool:
        return self.status == 410 or self.reason in ('BadDeviceToken', 'Unregistered',
                                                     'DeviceTokenNotForTopic')


def load_config() -> Config | None:
    path = os.environ.get('APNS_KEY_PATH', '')
    key_id = os.environ.get('APNS_KEY_ID', '')
    team_id = os.environ.get('APNS_TEAM_ID', '')
    topic = os.environ.get('APNS_TOPIC', '')
    if not (path and key_id and team_id and topic):
        return None
    try:
        with open(os.path.expanduser(path)) as f:
            key = f.read()
    except OSError:
        logger.warning('APNS_KEY_PATH is set but unreadable: %s', path)
        return None
    return Config(key, key_id, team_id, topic)


def configured() -> bool:
    return load_config() is not None


def provider_token(config: Config, now: float | None = None) -> str:
    global _token_cache
    now = now or time.time()
    with _token_lock:
        if _token_cache and now - _token_cache[1] < TOKEN_TTL:
            return _token_cache[0]
        token = jwt.encode({'iss': config.team_id, 'iat': int(now)}, config.key,
                           algorithm='ES256', headers={'kid': config.key_id})
        _token_cache = (token, now)
        return token


def reset_token_cache() -> None:
    global _token_cache
    with _token_lock:
        _token_cache = None


def payload(title: str, body: str, data: dict | None = None, *,
            thread: str = '') -> dict:
    aps = {'alert': {'title': title, 'body': body}, 'sound': 'default'}
    if thread:
        aps['thread-id'] = thread
    return {'aps': aps, **(data or {})}


def _client():
    import httpx
    return httpx.Client(http2=True, timeout=TIMEOUT)


def send(config: Config, device_token: str, body: dict, *, environment: str = 'production',
         collapse_id: str = '', client=None) -> Result:
    url = f"{HOSTS[environment]}/3/device/{device_token}"
    headers = {
        'authorization': f'bearer {provider_token(config)}',
        'apns-topic': config.topic,
        'apns-push-type': 'alert',
        'apns-priority': '10',
    }
    if collapse_id:
        headers['apns-collapse-id'] = collapse_id[:64]
    own = client is None
    client = client or _client()
    try:
        resp = client.post(url, headers=headers, content=json.dumps(body))
    except Exception as e:
        logger.warning('APNs request failed: %s', e)
        return Result(False, 0, str(e)[:200])
    finally:
        if own:
            client.close()
    if resp.status_code == 200:
        return Result(True, 200)
    try:
        reason = resp.json().get('reason', '')
    except ValueError:
        reason = resp.text[:200]
    if resp.status_code == 403 and reason == 'ExpiredProviderToken':
        reset_token_cache()
    return Result(False, resp.status_code, reason)
