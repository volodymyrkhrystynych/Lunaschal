"""Liking a forum chapter from the Library, and re-getting one chapter."""
from types import SimpleNamespace

import pytest

from backend.db.connection import get_db
from backend.fanfic import download, likes
from backend.fanfic.xenforo import parse_reader_page
from backend.tests.test_fanfic_import import THREAD, _import_fic, fake_net  # noqa: F401
from backend.tests.test_fanfic_reactions import _button


def _post_page(post_id: str, reaction: str | None, *, logged_in=True, body='Edited text',
               reaction_id='1', csrf='tok,123') -> str:
    button = _button(reaction).replace('reaction_id=1', f'reaction_id={reaction_id}').replace(
        'data-reaction-id="1"', f'data-reaction-id="{reaction_id}"')
    return (f'<html data-logged-in="{"true" if logged_in else "false"}" data-csrf="{csrf}"><body>'
            f'<article class="message" data-content="post-{post_id}">'
            f'<div class="bbWrapper">{body}</div>{button}</article></body></html>')


@pytest.fixture
def forum(client, fake_net, monkeypatch):  # noqa: F811
    """An imported fic whose posts can be served one at a time, with every
    reaction POST recorded instead of sent."""
    monkeypatch.setattr(likes, '_run_bg', lambda target, *args: target(*args))
    fic_id = _import_fic(client)
    pages = fake_net['pages']
    posts: list[tuple[str, dict]] = []

    def post_form(url, data):
        posts.append((url, data))
        return SimpleNamespace(json=lambda: {'status': 'ok'})
    monkeypatch.setattr(download, '_post_form', post_form)
    chapters = {r['source_post_id']: r['id'] for r in get_db().execute(
        'SELECT id, source_post_id FROM fic_chapters WHERE fic_id=?', (fic_id,))}
    return SimpleNamespace(fic_id=fic_id, pages=pages, posts=posts, chapters=chapters)


def _reaction(chapter_id):
    return get_db().execute('SELECT * FROM fic_chapter_reactions WHERE chapter_id=?',
                            (chapter_id,)).fetchone()


# --- parser ---

def test_parser_reads_the_csrf_token_and_the_reaction_id_to_send_back():
    page = parse_reader_page(_post_page('5', 'Informative', reaction_id='7'))
    assert page.csrf == 'tok,123'
    assert page.posts[0].my_reaction == 'Informative'
    assert page.posts[0].my_reaction_id == '7'


# --- liking ---

def test_a_like_from_the_app_is_posted_once_the_page_shows_none(client, forum):
    chapter = forum.chapters['101']
    forum.pages[f'{THREAD}/post-101'] = _post_page('101', None)
    resp = client.post(f'/api/fanfic/chapters/{chapter}/like', json={'liked': True})
    assert resp.status_code == 200
    url, data = forum.posts[0]
    assert url.endswith('/posts/101/react?reaction_id=1')
    assert data['_xfToken'] == 'tok,123'
    row = _reaction(chapter)
    assert (row['reaction'], row['source'], row['pending']) == ('Like', 'site', None)


def test_a_replayed_like_never_toggles_the_forums_like_back_off(client, forum):
    # The first request landed but its reply was lost: the page already shows
    # the like, so the retry must record it rather than POST (which toggles).
    chapter = forum.chapters['101']
    forum.pages[f'{THREAD}/post-101'] = _post_page('101', 'Like')
    client.post(f'/api/fanfic/chapters/{chapter}/like', json={'liked': True})
    assert forum.posts == []
    assert _reaction(chapter)['pending'] is None


def test_unliking_sends_back_the_users_own_reaction(client, forum):
    chapter = forum.chapters['101']
    forum.pages[f'{THREAD}/post-101'] = _post_page('101', 'Informative', reaction_id='7')
    client.post(f'/api/fanfic/chapters/{chapter}/like', json={'liked': False})
    assert forum.posts[0][0].endswith('/posts/101/react?reaction_id=7')
    assert _reaction(chapter)['reaction'] is None


def test_a_logged_out_page_parks_the_like_instead_of_posting(client, forum):
    chapter = forum.chapters['101']
    forum.pages[f'{THREAD}/post-101'] = _post_page('101', None, logged_in=False)
    client.post(f'/api/fanfic/chapters/{chapter}/like', json={'liked': True})
    assert forum.posts == []
    row = _reaction(chapter)
    assert row['pending'] == 'like' and 'not logged in' in row['push_error']
    assert row['reaction'] == 'Like'  # still shown as asked for
    assert not likes.has_runnable(get_db())  # wants cookies, not a timer
    # Asking again tries again.
    forum.pages[f'{THREAD}/post-101'] = _post_page('101', None)
    client.post(f'/api/fanfic/chapters/{chapter}/like', json={'liked': True})
    assert len(forum.posts) == 1 and _reaction(chapter)['pending'] is None


def test_a_transient_failure_stays_pending_with_a_retry_time(client, forum, monkeypatch):
    import requests
    chapter = forum.chapters['101']

    def down(url):
        raise requests.ConnectionError('reset')
    monkeypatch.setattr(download, '_fetch', down)
    client.post(f'/api/fanfic/chapters/{chapter}/like', json={'liked': True})
    row = _reaction(chapter)
    assert row['pending'] == 'like' and row['push_attempts'] == 1 and row['push_after'] > 0


def test_a_pending_like_survives_a_walk_over_an_older_page(client, forum, monkeypatch):
    chapter = forum.chapters['101']
    monkeypatch.setattr(likes, '_run_bg', lambda target, *args: None)  # not pushed yet
    client.post(f'/api/fanfic/chapters/{chapter}/like', json={'liked': True})
    post = parse_reader_page(_post_page('101', None)).posts[0]
    db = get_db()
    download._record_reaction(db, forum.fic_id, chapter, post, True)
    row = _reaction(chapter)
    assert (row['reaction'], row['pending']) == ('Like', 'like')


def test_opening_on_site_assumes_a_like_and_sends_nothing(client, forum):
    chapter = forum.chapters['102']
    resp = client.post(f'/api/fanfic/chapters/{chapter}/like', json={'liked': True, 'assumed': True})
    assert resp.get_json()['source'] == 'assumed'
    assert forum.posts == []
    row = _reaction(chapter)
    assert (row['reaction'], row['source'], row['pending']) == ('Like', 'assumed', None)
    # The next look at the page is the truth, either way.
    post = parse_reader_page(_post_page('102', None)).posts[0]
    download._record_reaction(get_db(), forum.fic_id, chapter, post, True)
    row = _reaction(chapter)
    assert (row['reaction'], row['source']) == (None, 'site')


def test_an_assumed_like_never_overrides_a_reaction_the_forum_showed(client, forum):
    chapter = forum.chapters['101']
    db = get_db()
    db.execute("INSERT INTO fic_chapter_reactions(chapter_id, fic_id, reaction, checked_at)"
               " VALUES (?,?,'Informative',5)", (chapter, forum.fic_id))
    db.commit()
    client.post(f'/api/fanfic/chapters/{chapter}/like', json={'liked': True, 'assumed': True})
    row = _reaction(chapter)
    assert (row['reaction'], row['source'], row['checked_at']) == ('Informative', 'site', 5)


def test_only_forum_chapters_can_be_liked(client):
    db = get_db()
    db.execute("INSERT INTO fics(id, title, source_type, created_at, updated_at)"
               " VALUES ('01J00000000000000000000001', 'Book', 'epub', 1, 1)")
    db.execute("INSERT INTO fic_chapters(id, fic_id, position, title, content_html, content_text,"
               " created_at) VALUES ('01J00000000000000000000002', '01J00000000000000000000001',"
               " 1, 'One', '', '', 1)")
    db.commit()
    resp = client.post('/api/fanfic/chapters/01J00000000000000000000002/like', json={'liked': True})
    assert resp.status_code == 400
    assert client.post('/api/fanfic/chapters/nope/like', json={'liked': True}).status_code == 404


def test_the_chapter_list_carries_the_like(client, forum):
    chapter = forum.chapters['102']
    client.post(f'/api/fanfic/chapters/{chapter}/like', json={'liked': True, 'assumed': True})
    rows = {c['id']: c for c in client.get(f'/api/fanfic/{forum.fic_id}/chapters').get_json()}
    assert rows[chapter]['reaction'] == 'Like' and rows[chapter]['likeable'] is True


# --- recheck ---

def test_recheck_queues_a_deep_check_of_forum_fics_with_a_session(client, forum, monkeypatch):
    from backend.routes import fanfic as fanfic_routes
    monkeypatch.setattr(fanfic_routes, '_start_drain_bg', lambda: None)
    assert client.post('/api/fanfic/reactions/recheck').get_json()['queued'] == 0
    client.put('/api/fanfic/cookies', json={'domain': 'forums.spacebattles.com', 'cookie': 'xf_user=u1'})
    assert client.post('/api/fanfic/reactions/recheck').get_json()['queued'] == 1
    row = get_db().execute('SELECT update_pending, deep_pending FROM fics WHERE id=?',
                           (forum.fic_id,)).fetchone()
    assert (row['update_pending'], row['deep_pending']) == (1, 1)


# --- re-getting a chapter ---

def test_refetch_rewrites_one_chapter_in_place(client, forum):
    chapter = forum.chapters['102']
    db = get_db()
    before = db.execute('SELECT position, title FROM fic_chapters WHERE id=?', (chapter,)).fetchone()
    db.execute('INSERT INTO fic_chapter_reads(chapter_id, fic_id, created_at) VALUES (?,?,1)',
               (chapter, forum.fic_id))
    db.commit()
    forum.pages[f'{THREAD}/post-102'] = _post_page('102', 'Like', body='A fixed typo')
    resp = client.post(f'/api/fanfic/chapters/{chapter}/refetch')
    assert resp.status_code == 202
    assert client.get(f'/api/fanfic/chapters/{chapter}/refetch').get_json() == {'done': True, 'error': None}
    after = db.execute('SELECT * FROM fic_chapters WHERE id=?', (chapter,)).fetchone()
    assert 'A fixed typo' in after['content_html']
    assert (after['position'], after['title']) == (before['position'], before['title'])
    assert db.execute('SELECT 1 FROM fic_chapter_reads WHERE chapter_id=?', (chapter,)).fetchone()
    assert _reaction(chapter)['reaction'] == 'Like'


def test_refetch_reports_a_missing_post(client, forum):
    chapter = forum.chapters['102']
    forum.pages[f'{THREAD}/post-102'] = _post_page('999', None)
    client.post(f'/api/fanfic/chapters/{chapter}/refetch')
    assert 'not on the page' in client.get(f'/api/fanfic/chapters/{chapter}/refetch').get_json()['error']
