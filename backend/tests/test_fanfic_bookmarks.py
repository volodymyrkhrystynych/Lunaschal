"""Route-level tests for chapter bookmarks (favorite / continue)."""
import pytest

from backend.tests.test_fanfic_routes import fanfic_root, make_fic  # noqa: F401


def test_create_favorite_bookmark(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'text')])
    resp = client.post(
        f'/api/fanfic/{fic_id}/bookmarks',
        json={'chapterId': chapter_ids[0], 'type': 'favorite', 'scrollPosition': 0.42})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ficId'] == fic_id
    assert body['chapterId'] == chapter_ids[0]
    assert body['type'] == 'favorite'
    assert body['scrollPosition'] == pytest.approx(0.42)
    assert body['chapterTitle'] == 'One'

    rows = client.get(f'/api/fanfic/{fic_id}/bookmarks').get_json()
    assert len(rows) == 1
    assert rows[0]['id'] == body['id']


def test_multiple_favorites_allowed(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'a'), ('Two', 'b')])
    client.post(f'/api/fanfic/{fic_id}/bookmarks',
                json={'chapterId': chapter_ids[0], 'type': 'favorite', 'scrollPosition': 0})
    client.post(f'/api/fanfic/{fic_id}/bookmarks',
                json={'chapterId': chapter_ids[1], 'type': 'favorite', 'scrollPosition': 0.5})
    rows = client.get(f'/api/fanfic/{fic_id}/bookmarks').get_json()
    assert len(rows) == 2


def test_new_continue_bookmark_replaces_old(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'a'), ('Two', 'b')])
    first = client.post(
        f'/api/fanfic/{fic_id}/bookmarks',
        json={'chapterId': chapter_ids[0], 'type': 'continue', 'scrollPosition': 0.1}
    ).get_json()
    second = client.post(
        f'/api/fanfic/{fic_id}/bookmarks',
        json={'chapterId': chapter_ids[1], 'type': 'continue', 'scrollPosition': 0.9}
    ).get_json()
    assert first['id'] != second['id']

    rows = client.get(f'/api/fanfic/{fic_id}/bookmarks').get_json()
    continues = [r for r in rows if r['type'] == 'continue']
    assert len(continues) == 1
    assert continues[0]['id'] == second['id']
    assert continues[0]['chapterId'] == chapter_ids[1]


def test_continue_bookmark_independent_per_fic(client):
    fic_a, chapters_a = make_fic('A', chapters=[('One', 'a')])
    fic_b, chapters_b = make_fic('B', chapters=[('One', 'b')])
    client.post(f'/api/fanfic/{fic_a}/bookmarks',
                json={'chapterId': chapters_a[0], 'type': 'continue', 'scrollPosition': 0})
    client.post(f'/api/fanfic/{fic_b}/bookmarks',
                json={'chapterId': chapters_b[0], 'type': 'continue', 'scrollPosition': 0})
    assert len(client.get(f'/api/fanfic/{fic_a}/bookmarks').get_json()) == 1
    assert len(client.get(f'/api/fanfic/{fic_b}/bookmarks').get_json()) == 1


def test_delete_bookmark(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'text')])
    bm = client.post(
        f'/api/fanfic/{fic_id}/bookmarks',
        json={'chapterId': chapter_ids[0], 'type': 'continue', 'scrollPosition': 0.2}
    ).get_json()

    resp = client.delete(f'/api/fanfic/bookmarks/{bm["id"]}')
    assert resp.status_code == 200
    assert client.get(f'/api/fanfic/{fic_id}/bookmarks').get_json() == []
    assert client.delete(f'/api/fanfic/bookmarks/{bm["id"]}').status_code == 404


def test_bookmarks_cascade_on_fic_delete(client, fanfic_root):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'text')])
    client.post(f'/api/fanfic/{fic_id}/bookmarks',
                json={'chapterId': chapter_ids[0], 'type': 'favorite', 'scrollPosition': 0})
    assert client.delete(f'/api/fanfic/{fic_id}').status_code == 200
    assert client.get(f'/api/fanfic/{fic_id}/bookmarks').get_json() == []


def test_create_validation(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'text')])
    # bad type
    assert client.post(
        f'/api/fanfic/{fic_id}/bookmarks',
        json={'chapterId': chapter_ids[0], 'type': 'nope', 'scrollPosition': 0}
    ).status_code == 400
    # missing chapterId
    assert client.post(
        f'/api/fanfic/{fic_id}/bookmarks',
        json={'type': 'favorite', 'scrollPosition': 0}
    ).status_code == 400
    # chapter belongs to a different fic
    other_fic, other_chapters = make_fic('Other', chapters=[('X', 'y')])
    assert client.post(
        f'/api/fanfic/{fic_id}/bookmarks',
        json={'chapterId': other_chapters[0], 'type': 'favorite', 'scrollPosition': 0}
    ).status_code == 404


def test_scroll_position_clamped(client):
    fic_id, chapter_ids = make_fic(chapters=[('One', 'text')])
    resp = client.post(
        f'/api/fanfic/{fic_id}/bookmarks',
        json={'chapterId': chapter_ids[0], 'type': 'favorite', 'scrollPosition': 5})
    assert resp.get_json()['scrollPosition'] == 1.0
    resp = client.post(
        f'/api/fanfic/{fic_id}/bookmarks',
        json={'chapterId': chapter_ids[0], 'type': 'favorite', 'scrollPosition': -3})
    assert resp.get_json()['scrollPosition'] == 0.0
