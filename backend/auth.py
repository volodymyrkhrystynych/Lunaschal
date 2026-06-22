import os
from datetime import datetime, timezone, timedelta
from functools import wraps

import bcrypt
import jwt
from flask import request, jsonify, make_response

JWT_SECRET = os.environ.get('JWT_SECRET', 'lunaschal-dev-secret-change-in-production')
COOKIE_NAME = 'lunaschal_token'
TOKEN_EXPIRY_DAYS = 7


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()


def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def generate_token() -> str:
    payload = {
        'authenticated': True,
        'exp': datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRY_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


def verify_token(token: str) -> bool:
    try:
        jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return True
    except jwt.InvalidTokenError:
        return False


def _is_localhost() -> bool:
    host = request.host or ''
    return host.startswith('localhost') or host.startswith('127.0.0.1')


def set_auth_cookie(response, token: str):
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite='Lax',
        max_age=TOKEN_EXPIRY_DAYS * 24 * 3600,
        secure=os.environ.get('APP_ENV') == 'production',
    )


def clear_auth_cookie(response):
    response.delete_cookie(COOKIE_NAME)


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if os.environ.get('APP_ENV') != 'production' and _is_localhost():
            return f(*args, **kwargs)
        token = request.cookies.get(COOKIE_NAME)
        if not token or not verify_token(token):
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated
