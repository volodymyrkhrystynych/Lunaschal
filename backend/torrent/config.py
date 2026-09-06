"""Torrent settings, read from the `settings` singleton.

Mirrors backend/ai/provider.py's shape: one function that turns the row into a
dict with every default already applied, so no caller has to know which columns
may be NULL.

The ProtonVPN WireGuard key is deliberately *not* here. It lives in a gitignored
`torrent/.env` next to the compose file that consumes it — this database is ~3G,
is rsynced to the backup drive nightly, and scripts/seed_test_db.py ships in a
public repo.
"""

# 8081, not qBittorrent's usual 8080: llama-server owns :8080 in this project
# (backend/ai/provider.py defaults llama_url there), so 8080 can never work.
DEFAULT_CLIENT_URL = 'http://127.0.0.1:8081'
DEFAULT_VPN_URL = 'http://127.0.0.1:8000'


def get_settings() -> dict | None:
    from backend.db.connection import get_db
    row = get_db().execute('SELECT * FROM settings LIMIT 1').fetchone()
    return dict(row) if row else None


def get_torrent_config() -> dict:
    s = get_settings() or {}
    return {
        'client_url': (s.get('torrent_client_url') or '').strip() or DEFAULT_CLIENT_URL,
        'username': (s.get('torrent_username') or '').strip(),
        'password': s.get('torrent_password') or '',
        'vpn_url': (s.get('torrent_vpn_url') or '').strip() or DEFAULT_VPN_URL,
        # On by default. The container cannot leak even with this off — the kill
        # switch is the network namespace, not this flag — but a tunnel that is
        # down means the client is simply offline, and refusing the add says so
        # immediately instead of leaving a torrent stalled at 0% looking like a
        # dead swarm.
        'require_vpn': bool(s.get('torrent_require_vpn', 1)),
        # 0 / NULL means "keep forever", which is the default: silently deleting
        # someone's downloads is not a reasonable thing to opt people into.
        'retention_days': int(s.get('torrent_default_retention_days') or 0),
        'ratio_limit': s.get('torrent_default_ratio_limit'),
        'seeding_minutes': s.get('torrent_default_seeding_minutes'),
    }
