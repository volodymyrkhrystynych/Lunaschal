"""A forum chapter's stored HTML is derived from the post as served, and this
is the one place that derivation lives, so the download path and the local
restyle can never disagree about it.

Bump `CHAPTER_HTML_VERSION` whenever `render_forum_post`'s output changes for
the same input (a new normalization rule, a sanitizer allowlist change): the
next startup re-derives every chapter that kept its `source_html`."""

import time

from backend.fanfic import xenforo
from backend.fanfic.sanitize import count_words, html_to_text, sanitize_chapter_html

CHAPTER_HTML_VERSION = 1


def render_forum_post(source_html: str) -> str:
    return sanitize_chapter_html(xenforo.normalize_bbcode_html(source_html))


def restyle_chapters(db, fic_id: str | None = None) -> int:
    """Re-render chapters from `source_html`, writing only those whose output
    changed (so `updated_at` moves, and the phone's replica re-sends, only for
    chapters that actually look different). Returns how many were rewritten."""
    sql = 'SELECT id, content_html, source_html FROM fic_chapters WHERE source_html IS NOT NULL'
    args: tuple = ()
    if fic_id is not None:
        sql += ' AND fic_id=?'
        args = (fic_id,)
    changed = 0
    now = int(time.time())
    for row in db.execute(sql, args).fetchall():
        clean = render_forum_post(row['source_html'])
        if clean == row['content_html']:
            continue
        text = html_to_text(clean)
        db.execute(
            'UPDATE fic_chapters SET content_html=?, content_text=?, word_count=?, updated_at=?'
            ' WHERE id=?',
            (clean, text, count_words(text), now, row['id']),
        )
        changed += 1
    db.commit()
    return changed
