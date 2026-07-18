# External Access (Internet-Facing Setup)

This guide covers exposing Lunaschal securely to the internet so you can reach it from outside your LAN. For a simpler alternative with no app changes, use Tailscale instead.

## Prerequisites

- A domain name or a free dynamic-DNS hostname (e.g. DuckDNS: `yourname.duckdns.org`)
- Port 80 and 443 forwarded on your router to the server machine
- [Caddy](https://caddyserver.com/docs/install) installed on the server

---

## 1. Dynamic DNS (skip if you have a static IP or domain)

[DuckDNS](https://www.duckdns.org) is free and works well. After registering:

```bash
# cron every 5 minutes to keep the DNS record pointing at your current IP
*/5 * * * * curl -s "https://www.duckdns.org/update?domains=yourname&token=YOUR_TOKEN&ip=" > /dev/null
```

---

## 2. Caddy reverse proxy

Caddy handles HTTPS automatically (Let's Encrypt, auto-renewal). Create `/etc/caddy/Caddyfile`:

```
yourname.duckdns.org {
    reverse_proxy 127.0.0.1:5000
}
```

Start/reload:

```bash
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

Caddy will obtain a certificate on first request. Flask stays on `127.0.0.1:5000` — only Caddy is internet-facing.

---

## 3. Flask cookie security

With HTTPS in front, the `secure` flag on the auth cookie must be enabled. In `backend/routes/auth.py`, change the `set_cookie` call:

```python
resp.set_cookie(
    COOKIE_NAME,
    make_token(),
    max_age=30 * 86400,
    httponly=True,
    samesite='Lax',
    secure=True,          # was False
)
```

Also set `SESSION_COOKIE_SECURE = True` if you add Flask sessions later.

---

## 4. Rate limiting on login

Without rate limiting, the login endpoint is open to brute-force. Add `Flask-Limiter`:

```bash
pip install Flask-Limiter
```

In `backend/app.py`:

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(get_remote_address, app=app, default_limits=[])
```

In `backend/routes/auth.py`:

```python
from backend.app import limiter   # or pass limiter in via app factory

@bp.post('/login')
@limiter.limit('10 per minute; 30 per hour')
def login():
    ...
```

---

## 5. Stronger second factor (optional but recommended)

The current "display code" is a static 6-digit value stored in the DB — it doesn't rotate on its own, making it weak over the internet. Replace it with a real TOTP (time-based, changes every 30 s):

```bash
pip install pyotp qrcode[pil]
```

**Generate a TOTP secret once and store it in the DB / env:**

```python
import pyotp
secret = pyotp.random_base32()   # store this in LUNASCHAL_TOTP_SECRET env var
```

**Verify in login route:**

```python
import pyotp, os

totp = pyotp.TOTP(os.environ['LUNASCHAL_TOTP_SECRET'])
if not totp.verify(code):          # code is the 6-digit value from authenticator app
    return jsonify({'error': 'Incorrect password or code'}), 401
```

**Enroll your authenticator app:** generate a QR code once (e.g. at `/api/auth/totp-setup`, guarded by localhost-only) and scan it with Google Authenticator / Aegis / 1Password.

---

## 6. Docker (protecting host files)

Running Flask inside a Docker container limits what a compromised app can reach on your machine. The key is to mount **only** the `data/` directory — the container's filesystem is otherwise isolated, so the file editor route can't escape to your home directory even if exploited.

**`docker-compose.yml` (minimal):**

```yaml
services:
  lunaschal:
    build: .
    ports:
      - '127.0.0.1:5000:5000' # bind to localhost only — Caddy proxies in
    volumes:
      - ./data:/app/data # only the SQLite DB is shared with the host
    environment:
      - NETWORK_MODE=1
      - LUNASCHAL_PASSWORD=${LUNASCHAL_PASSWORD}
      - JWT_SECRET=${JWT_SECRET}
      - LUNASCHAL_TOTP_SECRET=${LUNASCHAL_TOTP_SECRET}
    restart: unless-stopped
```

Note that `127.0.0.1:5000:5000` prevents Docker from exposing port 5000 on `0.0.0.0` — only Caddy (running on the host) can reach it.

---

## 7. Physical-presence device approval

For a personal desktop machine you want to protect from remote access without your knowledge, the strongest first-connection control is requiring you to physically click **Approve** on the machine before a new remote device is granted a session. This is a "trust on first use" model with a mandatory physical-presence check.

**How it works:**

1. An unknown remote IP hits the server with no valid JWT.
2. Instead of a plain 401, the backend creates a short-lived **pending approval** record (keyed by a one-time token) and returns `202 Pending` to the remote client.
3. An SSE event is pushed to the locally-running Lunaschal UI, which pops up a modal: _"Connection request from 1.2.3.4 — approve? (auto-reject in 5 s)"_ with a countdown.
4. The remote client polls a `/api/auth/approval-status/<token>` endpoint.
5. **Approved** (user clicks the button): backend issues a JWT bound to a device fingerprint; the remote client stores it and future connections skip re-approval.  
   **Rejected or timeout**: the pending record is deleted, the remote client receives 401.

**Device fingerprint** (stored in `localStorage` on the remote browser):

- A random UUID generated on first visit and sent as a custom header `X-Device-Id`.
- The backend stores approved device IDs in the `settings` table (or a dedicated `approved_devices` table) so subsequent connections from the same device are recognised without re-approval.

**Key implementation pieces:**

| Piece                                        | Location                              |
| -------------------------------------------- | ------------------------------------- |
| Pending approvals dict (in-memory, TTL 30 s) | `backend/auth.py`                     |
| `POST /api/auth/request-approval`            | new route in `backend/routes/auth.py` |
| `GET /api/auth/approval-status/<token>`      | same file                             |
| `POST /api/auth/approve/<token>`             | same file, localhost-only             |
| SSE push to local UI                         | reuse existing SSE infrastructure     |
| Approval modal with countdown                | new component in `src/components/`    |

**Timeout is configurable** — 5 s is aggressive (good for security; you must be at the machine), but 30 s is friendlier if you might be a few steps away.

---

## 8. Launch command

```bash
NETWORK_MODE=1 LUNASCHAL_PASSWORD=yourpassword python main.py
```

With TOTP:

```bash
NETWORK_MODE=1 LUNASCHAL_PASSWORD=yourpassword LUNASCHAL_TOTP_SECRET=BASE32SECRET python main.py
```

With Docker Compose:

```bash
LUNASCHAL_PASSWORD=yourpassword JWT_SECRET=... LUNASCHAL_TOTP_SECRET=... docker compose up -d
```

---

## Security checklist

- [ ] HTTPS via Caddy (cert auto-managed)
- [ ] `secure=True` on the auth cookie
- [ ] Rate limiting on `/api/auth/login`
- [ ] Strong `LUNASCHAL_PASSWORD` (20+ chars)
- [ ] TOTP second factor (replaces static display code)
- [ ] `JWT_SECRET` env var set to a random value (default is a dev string)
- [ ] Port 5000 NOT directly exposed — only Caddy on 443
- [ ] Docker: only `./data` mounted, port bound to `127.0.0.1` only
- [ ] Physical-presence device approval for first remote connection
