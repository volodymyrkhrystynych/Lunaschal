"""Pull the infohash and display name out of a magnet link.

Parsed here rather than read back from the client after adding, because the
alternative is racy: `torrents/add` returns "Ok." without saying *what* it
added, so recovering the hash would mean diffing `torrents/info` before and
after and hoping nothing else changed in between. A magnet already carries the
hash; taking it from the link is exact.

Pure, so the encodings can be tested without a client.
"""

import base64
import binascii
import re
from urllib.parse import parse_qs, unquote, urlparse

_BTIH = re.compile(r'^urn:btih:([0-9a-zA-Z]+)$', re.IGNORECASE)
_HEX40 = re.compile(r'^[0-9a-f]{40}$')


class InvalidMagnet(ValueError):
    pass


def _normalize_hash(raw: str) -> str:
    """BitTorrent v1 infohashes travel two ways in the wild: 40 hex characters,
    or the same 20 bytes in base32 (32 characters). Both are valid and clients
    emit both, so both have to normalize to the one hex form the API uses."""
    value = raw.strip()
    if _HEX40.match(value.lower()):
        return value.lower()
    if len(value) == 32:
        try:
            decoded = base64.b32decode(value.upper())
        except (binascii.Error, ValueError) as e:
            raise InvalidMagnet(f'Unreadable base32 infohash: {raw}') from e
        if len(decoded) != 20:
            raise InvalidMagnet(f'Unreadable base32 infohash: {raw}')
        return decoded.hex()
    raise InvalidMagnet(f'Not a v1 infohash: {raw}')


def parse_magnet(uri: str) -> tuple[str, str]:
    """-> (lowercase hex infohash, display name). Raises InvalidMagnet."""
    text = (uri or '').strip()
    if not text.lower().startswith('magnet:'):
        raise InvalidMagnet('Not a magnet link.')

    query = parse_qs(urlparse(text).query, keep_blank_values=False)

    info_hash = None
    for xt in query.get('xt', []):
        match = _BTIH.match(xt.strip())
        if match:
            info_hash = _normalize_hash(match.group(1))
            break
    if info_hash is None:
        # A v2-only magnet uses urn:btmh. qBittorrent can take it, but we could
        # not key a row by it, so say so rather than silently dropping the link.
        raise InvalidMagnet('No BitTorrent v1 infohash (xt=urn:btih:) in this link.')

    names = query.get('dn') or []
    name = unquote(names[0]).strip() if names else ''
    return info_hash, name or info_hash


def split_magnets(text: str) -> list[str]:
    """One textarea of pasted links -> a list. Blank lines and stray whitespace
    are the normal case when pasting from a page, not an error."""
    return [line.strip() for line in (text or '').splitlines() if line.strip()]
