import os
import time

import jwt

NETWORK_MODE: bool = os.environ.get('NETWORK_MODE', '').lower() in ('1', 'true', 'yes')
JWT_SECRET: str = os.environ.get('JWT_SECRET', 'lunaschal-dev-secret-set-JWT_SECRET-in-production')
COOKIE_NAME = 'lunaschal_token'
_TOKEN_TTL = 30 * 86400  # 30 days


def is_localhost(request) -> bool:
    host = request.host.split(':')[0]
    return host in ('localhost', '127.0.0.1', '::1')


def make_token() -> str:
    now = int(time.time())
    return jwt.encode({'iat': now, 'exp': now + _TOKEN_TTL}, JWT_SECRET, algorithm='HS256')


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except jwt.InvalidTokenError:
        return None
