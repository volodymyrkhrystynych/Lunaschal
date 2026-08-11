"""The one coordinate validator, shared by the food log and chat photo uploads.

Two features validating latitude/longitude slightly differently is how one of
them ends up storing a NaN — a value no map query matches and no comparison
rejects.
"""
import pytest

from backend.geo import coord_pair, parse_coord


@pytest.mark.parametrize('raw,expected', [
    (43.6446, 43.6446),
    ('43.6446', 43.6446),
    (0, 0.0),
    (-180, -180.0),
    (180, 180.0),
])
def test_accepts_real_coordinates(raw, expected):
    assert parse_coord(raw) == expected


@pytest.mark.parametrize('raw', [None, '', 'nan', 'inf', '-inf', 'here', 180.1, -180.1, {}])
def test_rejects_everything_else(raw):
    assert parse_coord(raw) is None


def test_a_pair_needs_both_halves():
    assert coord_pair('43.6446', '-79.3975') == (43.6446, -79.3975)
    # A lone latitude produces a row that looks located and isn't.
    assert coord_pair('43.6446', None) is None
    assert coord_pair(None, '-79.3975') is None
    assert coord_pair('nan', '-79.3975') is None
