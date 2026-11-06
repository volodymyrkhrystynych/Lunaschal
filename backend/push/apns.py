"""Apple Push Notification service: token-based auth over HTTP/2.

Used by the coach's reminders and the Code tab alike. Configured from the
environment, never from the database, so no API route or mobile-sync projection
can ever hand the key out:

- `APNS_KEY_PATH` — the .p8 signing key downloaded from the developer account
- `APNS_KEY_ID`   — that key's id
- `APNS_TEAM_ID`  — the team (defaults to the one in apple/project.yml)
- `APNS_TOPIC`    — the app's bundle id (defaults to com.lunaschal.mobile)

The provider token is an ES256 JWT that Apple accepts for an hour; it is
reused for 50 minutes and **not re-signed more often than every 20**, because
Apple answers `TooManyProviderTokenUpdates` to a sender that does. A device
token belongs to one environment — Xcode/debug builds register with the
sandbox, TestFlight and App Store builds with production — so each device row
says which host to use, and a mismatch comes back as `BadDeviceToken`.
"""
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

HOSTS = {
    'production': 'https://api.push.apple.com',
    'sandbox': 'https://api.sandbox.push.apple.com',
}
TOKEN_REUSE = 50 * 60
TOKEN_MIN_AGE = 20 * 60
DEFAULT_TEAM = '4AG98Q33RQ'
DEFAULT_TOPIC = 'com.lunaschal.mobile'
# Device tokens Apple says will never work again.
DEAD_REASONS = {'BadDeviceToken', 'Unregistered', 'DeviceTokenNotForTopic'}


@dataclass(frozen=True)
class Config:
    key: str
    key_id: str
    team_id: str
    topic: str


def config() -> Config | None:
    path, key_id = os.environ.get('APNS_KEY_PATH'), os.environ.get('APNS_KEY_ID')
    if not path or not key_id:
        return None
    try:
        key = Path(path).read_text()
    except OSError:
        return None
    return Config(key=key, key_id=key_id, team_id=os.environ.get('APNS_TEAM_ID') or DEFAULT_TEAM,
                  topic=os.environ.get('APNS_TOPIC') or DEFAULT_TOPIC)


def configured() -> bool:
    return config() is not None


class ProviderToken:
    def __init__(self, cfg: Config, clock=time.time):
        self.cfg, self.clock = cfg, clock
        self._token: str | None = None
        self._issued = 0.0
        self._lock = threading.Lock()

    def get(self, *, expired: bool = False) -> str:
        """The current token; a new one once it is 50 minutes old, or when
        Apple called it expired and the old one is at least 20 minutes old."""
        with self._lock:
            age = self.clock() - self._issued
            if self._token is None or age >= TOKEN_REUSE or (expired and age >= TOKEN_MIN_AGE):
                import jwt
                self._issued = self.clock()
                self._token = jwt.encode({'iss': self.cfg.team_id, 'iat': int(self._issued)}, self.cfg.key,
                                         algorithm='ES256', headers={'kid': self.cfg.key_id})
            return self._token


@dataclass
class SendResult:
    status: int
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def dead(self) -> bool:
        """The device token will never work again: forget it."""
        return self.status == 410 or self.reason in DEAD_REASONS


class Client:
    def __init__(self, cfg: Config, http=None, clock=time.time):
        self.cfg = cfg
        self.tokens = ProviderToken(cfg, clock)
        if http is None:
            import httpx
            http = httpx.Client(http2=True, timeout=15)
        self.http = http

    def send(self, device_token: str, environment: str, payload: dict, *,
             collapse_id: str | None = None, expiration: int | None = None) -> SendResult:
        from backend.push.payload import encode
        host = HOSTS.get(environment)
        if host is None:
            return SendResult(400, f'unknown environment {environment!r}')
        result = None
        for attempt in range(2):
            headers = {
                'authorization': f'bearer {self.tokens.get(expired=attempt > 0)}',
                'apns-topic': self.cfg.topic,
                'apns-push-type': 'alert',
                'apns-priority': '10',
            }
            if collapse_id:
                headers['apns-collapse-id'] = collapse_id[:64]
            if expiration is not None:
                headers['apns-expiration'] = str(expiration)
            try:
                response = self.http.post(f'{host}/3/device/{device_token}', content=encode(payload),
                                          headers=headers)
            except Exception as exc:
                return SendResult(0, f'{type(exc).__name__}: {exc}'[:200])
            reason = None
            if response.status_code != 200:
                try:
                    reason = response.json().get('reason')
                except ValueError:
                    reason = response.text[:200] or None
            result = SendResult(response.status_code, reason)
            if not (response.status_code == 403 and reason == 'ExpiredProviderToken'):
                break
        return result


_client: Client | None = None
_client_lock = threading.Lock()


def client() -> Client | None:
    """The process's client, or None while APNs isn't configured."""
    global _client
    cfg = config()
    if cfg is None:
        return None
    with _client_lock:
        if _client is None or _client.cfg != cfg:
            _client = Client(cfg)
        return _client
