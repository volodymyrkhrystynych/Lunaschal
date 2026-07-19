import os
import subprocess
import time

import jwt

NETWORK_MODE: bool = os.environ.get('NETWORK_MODE', '').lower() in ('1', 'true', 'yes')
JWT_SECRET: str = os.environ.get('JWT_SECRET', 'lunaschal-dev-secret-set-JWT_SECRET-in-production')
COOKIE_NAME = 'lunaschal_token'
_TOKEN_TTL = 30 * 86400  # 30 days


def _self_tailscale_ips() -> set[str]:
    """This machine's own Tailscale IPs, so the desktop app (which now loads
    over the Tailscale HTTPS hostname instead of localhost, since the cert is
    bound to that hostname) can still bypass the network-mode login."""
    try:
        result = subprocess.run(['tailscale', 'ip'], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            return {line.strip() for line in result.stdout.splitlines() if line.strip()}
    except (OSError, subprocess.SubprocessError):
        pass
    return set()


# Only shell out to `tailscale` when it could actually matter (network mode);
# harmless if the binary is missing or the daemon isn't running.
_SELF_TAILSCALE_IPS: set[str] = _self_tailscale_ips() if NETWORK_MODE else set()


def is_localhost(request) -> bool:
    host = request.host.split(':')[0]
    if host in ('localhost', '127.0.0.1', '::1'):
        return True
    return request.remote_addr in _SELF_TAILSCALE_IPS


def make_token() -> str:
    now = int(time.time())
    return jwt.encode({'iat': now, 'exp': now + _TOKEN_TTL}, JWT_SECRET, algorithm='HS256')


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except jwt.InvalidTokenError:
        return None
