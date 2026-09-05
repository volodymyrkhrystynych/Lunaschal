"""Read the infohash and name out of a .torrent file.

Only as much bencode as that needs. The reason it needs *any* is that a v1
infohash is the SHA-1 of the `info` dictionary **exactly as it appears in the
file** — not of a re-encoding of the parsed value. Two encoders can emit
semantically identical dicts with different byte orders, and hashing a
round-tripped copy would silently produce a hash no peer recognises. So the
decoder tracks byte offsets and the hash is taken over the original slice.

Pure, so the awkward cases (nested lists, non-UTF-8 names, truncated files) are
testable without a client.
"""

import hashlib


class InvalidTorrentFile(ValueError):
    pass


def _decode(data: bytes, index: int):
    """-> (value, next_index). Values are bytes/int/list/dict."""
    if index >= len(data):
        raise InvalidTorrentFile('Truncated torrent file.')
    marker = data[index : index + 1]

    if marker == b'i':
        end = data.index(b'e', index)
        return int(data[index + 1 : end]), end + 1

    if marker == b'l':
        index += 1
        items = []
        while data[index : index + 1] != b'e':
            value, index = _decode(data, index)
            items.append(value)
        return items, index + 1

    if marker == b'd':
        index += 1
        out = {}
        while data[index : index + 1] != b'e':
            key, index = _decode(data, index)
            value, index = _decode(data, index)
            out[key] = value
        return out, index + 1

    if marker.isdigit():
        colon = data.index(b':', index)
        length = int(data[index:colon])
        start = colon + 1
        return data[start : start + length], start + length

    raise InvalidTorrentFile(f'Unexpected bencode marker {marker!r} at byte {index}.')


def info_hash_and_name(data: bytes) -> tuple[str, str]:
    """-> (lowercase hex v1 infohash, display name)."""
    if not data:
        raise InvalidTorrentFile('Empty file.')
    if data[:1] != b'd':
        raise InvalidTorrentFile('Not a torrent file (does not start with a bencode dict).')

    try:
        index = 1
        while data[index : index + 1] != b'e':
            key, index = _decode(data, index)
            value_start = index
            value, index = _decode(data, index)
            if key == b'info':
                # The original bytes, not a re-encoding — see the module note.
                info_hash = hashlib.sha1(data[value_start:index]).hexdigest()
                raw_name = value.get(b'name', b'') if isinstance(value, dict) else b''
                # Torrent names are conventionally UTF-8 but nothing enforces
                # it; a mojibake name is better than a 500.
                name = raw_name.decode('utf-8', errors='replace')
                return info_hash, name
    except InvalidTorrentFile:
        raise
    except (ValueError, IndexError, KeyError, TypeError) as e:
        raise InvalidTorrentFile(f'Malformed torrent file: {e}') from e

    raise InvalidTorrentFile('Torrent file has no info dictionary.')
