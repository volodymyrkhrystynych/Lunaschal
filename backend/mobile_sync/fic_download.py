"""One fic, on demand, ahead of the library download.

The bulk download walks the whole change log before a single book is
readable, so opening a fic used to show nothing until every chapter of every
fic had arrived. This serves one fic's chapters in position order, each at its
latest logged revision — the same change a bootstrap would carry, so the phone
stores them in its replica as if the bootstrap had already reached them.

Sizes ride along so the phone can show progress and a time estimate.
"""
from .feed import change_dict, database
from . import media

MAX_PAGE_CHAPTERS = 50
DEFAULT_PAGE_CHAPTERS = 20
# Bytes of chapter text a page aims for: one huge chapter still comes whole.
PAGE_BYTE_TARGET = 4 * 1024 * 1024

_BYTES = "length(CAST(COALESCE(content_html,'') AS BLOB))+length(CAST(COALESCE(content_text,'') AS BLOB))"


class FicNotFound(LookupError):
    pass


def _latest(db, collection, record_id):
    return db.execute(
        '''SELECT * FROM mobile_sync_changes WHERE collection=? AND record_id=?
           ORDER BY sequence DESC LIMIT 1''', (collection, record_id)).fetchone()


def _parse_after(after):
    if not after:
        return None
    if not isinstance(after, str) or len(after) > 128 or ':' not in after:
        raise ValueError('Invalid chapter page key')
    position, chapter_id = after.split(':', 1)
    try:
        return int(position), chapter_id
    except ValueError as exc:
        raise ValueError('Invalid chapter page key') from exc


def page(fic_id, *, after='', limit=DEFAULT_PAGE_CHAPTERS):
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_CHAPTERS:
        raise ValueError(f'limit must be between 1 and {MAX_PAGE_CHAPTERS}')
    key = _parse_after(after)
    with database() as db:
        fic = db.execute('SELECT * FROM fics WHERE id=?', (fic_id,)).fetchone()
        if fic is None:
            raise FicNotFound(fic_id)
        epoch = db.execute('SELECT id FROM mobile_sync_state').fetchone()['id']
        totals = db.execute(f'SELECT COUNT(*) AS n, COALESCE(SUM({_BYTES}),0) AS bytes FROM fic_chapters WHERE fic_id=?',
                            (fic_id,)).fetchone()
        where, args = 'fic_id=?', [fic_id]
        if key is not None:
            where += ' AND (position,id)>(?,?)'
            args += list(key)
        rows = db.execute(f'SELECT id,position,{_BYTES} AS bytes FROM fic_chapters WHERE {where} '
                          'ORDER BY position,id LIMIT ?', (*args, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        chosen, page_bytes = [], 0
        for row in rows:
            if chosen and page_bytes + row['bytes'] > PAGE_BYTE_TARGET:
                more = True
                break
            chosen.append(row)
            page_bytes += row['bytes']
        chapters = []
        for row in chosen:
            change = _latest(db, 'fic_chapters', row['id'])
            if change is not None:
                chapters.append(change_dict(change))
        before = 0
        if key is not None:
            before = db.execute(f'SELECT COALESCE(SUM({_BYTES}),0) FROM fic_chapters '
                                'WHERE fic_id=? AND (position,id)<=(?,?)', (fic_id, *key)).fetchone()[0]
        book = _latest(db, 'fics', fic_id)
    descriptor = media.describe('fics', fic)[0] if fic['source_type'] == 'pdf' else None
    last = chosen[-1] if chosen else None
    return {
        'epoch': epoch,
        'fic': change_dict(book) if book is not None else None,
        'chapters': chapters,
        'hasMore': more,
        'after': f"{last['position']}:{last['id']}" if last else (after or ''),
        'totalChapters': totals['n'],
        'textBytes': totals['bytes'],
        'bytesBefore': before,
        'pageBytes': page_bytes,
        'media': descriptor,
    }
