"""Unit tests for backend.tags.tag_counts — the {name, count} aggregation
shared by the cookbook and food /tags endpoints."""
import json

from backend.tags import tag_counts


def test_counts_and_sorts_by_count_desc_then_name():
    rows = [
        {'tags': json.dumps(['italian', 'dinner'])},
        {'tags': json.dumps(['italian'])},
        {'tags': json.dumps(['dinner', 'quick'])},
    ]
    assert tag_counts(rows) == [
        {'name': 'dinner', 'count': 2},
        {'name': 'italian', 'count': 2},
        {'name': 'quick', 'count': 1},
    ]


def test_ignores_malformed_or_null_tags():
    rows = [
        {'tags': None},
        {'tags': 'not json'},
        {'tags': json.dumps(['ok'])},
    ]
    assert tag_counts(rows) == [{'name': 'ok', 'count': 1}]


def test_ignores_non_string_and_blank_entries():
    rows = [{'tags': json.dumps(['ok', '', '  ', 5, None])}]
    assert tag_counts(rows) == [{'name': 'ok', 'count': 1}]


def test_empty_rows_returns_empty_list():
    assert tag_counts([]) == []


def test_custom_column_name():
    rows = [{'site_tags': json.dumps(['a', 'a', 'b'])}]
    assert tag_counts(rows, column='site_tags') == [
        {'name': 'a', 'count': 2},
        {'name': 'b', 'count': 1},
    ]
