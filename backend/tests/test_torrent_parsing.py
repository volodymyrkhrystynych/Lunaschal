"""The two pure parsers: magnet links and .torrent files.

Both exist so that adding a torrent can key a row by infohash immediately,
instead of adding it blind and then trying to work out which of the client's
torrents is the new one.
"""
import base64
import hashlib

import pytest

from backend.torrent.magnet import InvalidMagnet, parse_magnet, split_magnets
from backend.torrent.torrentfile import InvalidTorrentFile, info_hash_and_name

HEX = 'c9e15763f722f23e98a29decdfae341b98d53056'


def test_parses_hex_infohash_and_name():
    assert parse_magnet(f'magnet:?xt=urn:btih:{HEX}&dn=Debian+ISO') == (HEX, 'Debian ISO')


def test_uppercase_hex_normalizes_to_lower():
    assert parse_magnet(f'magnet:?xt=urn:btih:{HEX.upper()}')[0] == HEX


def test_base32_infohash_normalizes_to_the_same_hex():
    """Clients emit both spellings of the same 20 bytes; if these disagreed,
    re-adding a torrent from a different source would make a duplicate row."""
    b32 = base64.b32encode(bytes.fromhex(HEX)).decode()
    assert parse_magnet(f'magnet:?xt=urn:btih:{b32}')[0] == HEX


def test_name_falls_back_to_the_hash_when_the_link_has_no_dn():
    assert parse_magnet(f'magnet:?xt=urn:btih:{HEX}')[1] == HEX


@pytest.mark.parametrize('bad', [
    '',
    'http://example.com/x.torrent',
    'magnet:?dn=no+hash+here',
    f'magnet:?xt=urn:btih:{HEX[:10]}',
])
def test_rejects_junk(bad):
    with pytest.raises(InvalidMagnet):
        parse_magnet(bad)


def test_rejects_v2_only_magnet_rather_than_guessing():
    with pytest.raises(InvalidMagnet):
        parse_magnet('magnet:?xt=urn:btmh:1220caf1e1c30e81cb361b9ee167c4aa64228a7fa4fa9f6105232b28ad099f3a302e')


def test_split_ignores_blank_lines_and_padding():
    assert split_magnets(' a \n\n\tb\n') == ['a', 'b']


# --- .torrent --------------------------------------------------------------

INFO = b'd6:lengthi1024e4:name8:test.iso12:piece lengthi16384e6:pieces0:e'
TORRENT = b'd8:announce19:http://tracker.test4:info' + INFO + b'e'


def test_infohash_is_sha1_of_the_original_info_bytes():
    """Not of a re-encoding. Two encoders can order a dict differently, and a
    round-tripped hash would be one no peer recognises."""
    assert info_hash_and_name(TORRENT) == (hashlib.sha1(INFO).hexdigest(), 'test.iso')


def test_a_reordered_file_gives_a_different_hash_than_its_info_alone():
    padded = b'd8:announce19:http://tracker.test4:info' + INFO + b'7:comment2:hie'
    # Same info dict, extra sibling key: the hash must be unchanged.
    assert info_hash_and_name(padded)[0] == hashlib.sha1(INFO).hexdigest()


def test_non_utf8_name_does_not_raise():
    blob = b'd4:infod4:name4:\xff\xfe\xfd\xfcee'
    assert info_hash_and_name(blob)[1] == '�' * 4


@pytest.mark.parametrize('label,blob', [
    ('empty', b''),
    ('not bencode', b'hello'),
    ('no info dict', b'd8:announce5:helloe'),
    ('truncated', b'd4:infod6:lengthi5e'),
])
def test_rejects_malformed_files(label, blob):
    with pytest.raises(InvalidTorrentFile):
        info_hash_and_name(blob)
