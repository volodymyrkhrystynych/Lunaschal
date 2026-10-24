"""Liking a forum chapter from the Library, and re-getting one chapter.

A like asked for in the app is a row before it is a request: the chapter's
`fic_chapter_reactions` row takes the new state at once (so every client shows
it) with `pending` set, and a single worker makes it true on the forum. Three
rules keep that from ever doing the opposite of what was asked:

- **Read before writing.** XenForo's react endpoint *toggles* -- reacting with
  the reaction you already gave removes it -- so a blind POST replayed after a
  timeout un-likes the post. The worker fetches the post's page first and only
  POSTs when the forum disagrees with the request; a retry that finds the like
  already there just records it.
- **Only a logged-in page counts.** A page fetched without a session shows no
  react button, which must not read as "not liked yet" and be "fixed".
- **An assumed like is never pushed.** Opening a chapter on the site records
  it as liked (`source='assumed'`) because the user presses Like there
  themselves; the next walk over the page confirms or corrects it.

Re-getting a chapter is here too because it needs the same single post: the
post URL lands on the thread page that holds it, which is everything the
reader page would have said about it.
"""
import json
import logging
import threading
import time

from backend.db.connection import get_db
from backend.fanfic import download, xenforo

LIKE_REACTION_ID = '1'
# Seconds before a failed push is tried again; past the end it stays pending
# with its error showing until the user asks again.
RETRY_SCHEDULE = (60, 300, 1800, 7200)

_lock = threading.Lock()
_active = False
_refetch: dict[str, dict] = {}


def _run_bg(target, *args) -> None:
    """The one place a worker thread starts; tests run it inline."""
    threading.Thread(target=target, args=args, daemon=True).start()


class LikeError(ValueError):
    pass


def _chapter(db, chapter_id: str):
    return db.execute(
        'SELECT c.id, c.fic_id, c.source_post_id, f.source_type, f.source_url'
        ' FROM fic_chapters c JOIN fics f ON f.id=c.fic_id WHERE c.id=?',
        (chapter_id,)).fetchone()


def _forum_post(row) -> tuple[xenforo.ThreadRef, str]:
    if row is None:
        raise LookupError('Chapter not found')
    ref = xenforo.parse_thread_ref(row['source_url'] or '') if row['source_type'] == 'xenforo' else None
    if ref is None or not row['source_post_id']:
        raise LikeError('Only forum chapters can be liked or re-fetched')
    return ref, row['source_post_id']


def set_like(db, chapter_id: str, liked: bool, *, assumed: bool = False) -> dict:
    """Record the user's like (or un-like) of a chapter. Idempotent; the
    caller commits. An assumed like never overrides what the forum showed or
    what the user asked for, and never queues a request."""
    row = _chapter(db, chapter_id)
    _forum_post(row)
    current = db.execute('SELECT * FROM fic_chapter_reactions WHERE chapter_id=?', (chapter_id,)).fetchone()
    now = int(time.time())
    if assumed:
        if liked and (current is None or (current['reaction'] is None and current['pending'] is None)):
            db.execute(
                "INSERT INTO fic_chapter_reactions(chapter_id, fic_id, reaction, checked_at, source)"
                " VALUES (?,?,'Like',?,'assumed') ON CONFLICT(chapter_id) DO UPDATE SET"
                " reaction='Like', source='assumed', checked_at=excluded.checked_at",
                (chapter_id, row['fic_id'], now))
        return state(db, chapter_id)
    reaction = (current['reaction'] if current and current['reaction'] else 'Like') if liked else None
    # Already where the forum last showed it, and nothing in flight: there is
    # nothing to send. An assumed like asked for in earnest still is sent,
    # since nobody has confirmed the user pressed it.
    settled = (current is not None and current['pending'] is None and current['source'] == 'site'
               and (current['reaction'] is not None) == liked)
    pending = None if settled else ('like' if liked else 'unlike')
    db.execute(
        'INSERT INTO fic_chapter_reactions(chapter_id, fic_id, reaction, checked_at, source, pending,'
        ' push_error, push_attempts, push_after) VALUES (?,?,?,?,?,?,NULL,0,0)'
        ' ON CONFLICT(chapter_id) DO UPDATE SET reaction=excluded.reaction,'
        ' source=excluded.source, pending=excluded.pending, push_error=NULL,'
        ' push_attempts=0, push_after=0',
        (chapter_id, row['fic_id'], reaction, current['checked_at'] if current else now,
         'site' if settled else 'app', pending))
    return state(db, chapter_id)


def state(db, chapter_id: str) -> dict:
    row = db.execute('SELECT * FROM fic_chapter_reactions WHERE chapter_id=?', (chapter_id,)).fetchone()
    if row is None:
        return {'chapterId': chapter_id, 'reaction': None, 'checked': False, 'source': None,
                'pending': None, 'error': None}
    return {'chapterId': chapter_id, 'reaction': row['reaction'], 'checked': True,
            'source': row['source'], 'pending': row['pending'], 'error': row['push_error']}


def _post_page(ref: xenforo.ThreadRef, post_id: str):
    resp = download._fetch(ref.post_url(post_id))
    page = xenforo.parse_reader_page(resp.text)
    post = next((p for p in page.posts if p.post_id == post_id), None)
    if post is None:
        raise LikeError(f'Post {post_id} was not on the page it redirects to')
    return resp, page, post


def push_one(chapter_id: str) -> None:
    """Make one pending like true on the forum. Raises on failure; the row is
    only cleared once the forum is known to agree."""
    db = get_db()
    row = db.execute('SELECT * FROM fic_chapter_reactions WHERE chapter_id=?', (chapter_id,)).fetchone()
    if row is None or row['pending'] is None:
        return
    want = row['pending'] == 'like'
    ref, post_id = _forum_post(_chapter(db, chapter_id))
    _, page, post = _post_page(ref, post_id)
    if not page.logged_in or not post.reaction_seen:
        raise download.FetchBlockedError(
            f'{ref.domain}: not logged in, or this post has no Like button (your own post?).'
            ' Save fresh cookies in Settings -> Fanfic site cookies.')
    reaction = post.my_reaction
    if (reaction is not None) != want:
        if not page.csrf:
            raise LikeError('The page carried no security token to react with')
        reaction_id = (post.my_reaction_id or LIKE_REACTION_ID) if not want else LIKE_REACTION_ID
        resp = download._post_form(
            f'https://{ref.domain}/posts/{post_id}/react?reaction_id={reaction_id}',
            {'_xfToken': page.csrf, '_xfResponseType': 'json', '_xfWithData': '1'})
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and body.get('errors'):
            raise LikeError('; '.join(map(str, body['errors'])) if isinstance(body['errors'], list)
                            else json.dumps(body['errors']))
        reaction = 'Like' if want else None
    # Only clear the request this push answered: the user may have flipped it
    # again while the request was out.
    db.execute(
        "UPDATE fic_chapter_reactions SET reaction=?, source='site', pending=NULL, push_error=NULL,"
        ' push_attempts=0, push_after=0, checked_at=? WHERE chapter_id=? AND pending=?',
        (reaction, int(time.time()), chapter_id, row['pending']))
    db.commit()


_RUNNABLE = ('SELECT chapter_id, push_attempts FROM fic_chapter_reactions'
             ' WHERE pending IS NOT NULL AND push_after<=? AND push_attempts<=?'
             ' ORDER BY push_after, chapter_id LIMIT 1')


def run_pending() -> None:
    """Push every runnable pending like, one at a time."""
    db = get_db()
    while True:
        row = db.execute(_RUNNABLE, (int(time.time()), len(RETRY_SCHEDULE))).fetchone()
        if row is None:
            return
        try:
            push_one(row['chapter_id'])
        except Exception as exc:
            attempts = row['push_attempts'] + 1
            # A blocked page wants cookies, not a timer: park it until asked.
            wait = (RETRY_SCHEDULE[attempts - 1] if download.is_transient(exc)
                    and attempts <= len(RETRY_SCHEDULE) else None)
            db.execute(
                'UPDATE fic_chapter_reactions SET push_error=?, push_attempts=?, push_after=?'
                ' WHERE chapter_id=?',
                (str(exc), attempts if wait is not None else len(RETRY_SCHEDULE) + 1,
                 int(time.time()) + (wait or 0), row['chapter_id']))
            db.commit()
            logging.getLogger(__name__).warning('Fic like push failed for %s: %s', row['chapter_id'], exc)


def start_push() -> None:
    global _active
    with _lock:
        if _active:
            return
        _active = True

    def worker():
        global _active
        try:
            run_pending()
        finally:
            with _lock:
                _active = False

    _run_bg(worker)


def has_runnable(db) -> bool:
    return db.execute(_RUNNABLE, (int(time.time()), len(RETRY_SCHEDULE))).fetchone() is not None


# --- re-getting one chapter ---

def refetch_status(chapter_id: str) -> dict | None:
    with _lock:
        return dict(_refetch[chapter_id]) if chapter_id in _refetch else None


def refetch_chapter(chapter_id: str) -> None:
    """Rewrite one chapter from the forum in place, edited or not. The id,
    position, read mark and bookmarks stay; the reaction is read too."""
    try:
        db = get_db()
        row = _chapter(db, chapter_id)
        ref, post_id = _forum_post(row)
        resp, page, post = _post_page(ref, post_id)
        clean, text, _ = download.process_post_html(row['fic_id'], post.content_html, str(resp.url))
        download._update_chapter(db, chapter_id, post, clean, text)
        download._record_reaction(db, row['fic_id'], chapter_id, post, page.logged_in)
        db.commit()
        result = {'done': True, 'error': None}
    except Exception as exc:
        result = {'done': True, 'error': str(exc)}
    with _lock:
        _refetch[chapter_id] = result


def start_refetch(chapter_id: str) -> bool:
    """False when this chapter is already being re-fetched."""
    with _lock:
        if chapter_id in _refetch and not _refetch[chapter_id]['done']:
            return False
        _refetch[chapter_id] = {'done': False, 'error': None}
    _run_bg(refetch_chapter, chapter_id)
    return True
