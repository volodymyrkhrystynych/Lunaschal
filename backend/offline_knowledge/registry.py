"""The scanned state of the ZIM library: what is there, and what to do with it.

The archives on disk stay the source of truth and are never written to. This
table caches what a scan found so that the two hot paths stop touching the
filesystem:

* **search** used to rglob the root and open every archive in filename order,
  breaking once the global result limit was full -- which is the unfairness the
  design doc records as known limitation #1, and which becomes fatal the moment
  a 107 GB Stack Overflow sorts before `wikipedia_`;
* **reading one article** used to rglob the entire root inside `_resolve()`,
  once per request, to turn an id back into a path.

Both are now indexed SELECTs. The rglob happens in `sync()`, behind a TTL, and
the only thing that forces it is a config change or the rescan route.

`sync()` re-derives everything a ZIM can tell us about itself, which is why
`kind_source` exists: a rescan must not overwrite a correction the user made by
hand. It also never deletes a row. An archive whose file is gone is marked
`missing` and keeps its id, its kind and its enabled flag, so unplugging the
archive drive and plugging it back in is not the same event as being handed a
new library.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from backend.db.connection import get_db
from backend.offline_knowledge import archive as _archive_mod
from backend.offline_knowledge import kinds

logger = logging.getLogger(__name__)

# How long a scan is trusted before a search triggers another one. A sync that
# finds nothing changed costs one rglob and one SELECT -- no archive is opened
# unless its size moved -- so this is short enough that a file dropped into the
# folder shows up on its own, without a button.
SCAN_TTL_SECONDS = 60

_COLUMNS = (
    'id, zim_uuid, path, filename, title, language, zim_date, flavour, size, '
    'article_count, kind, kind_source, match_terms, enabled, '
    'has_fulltext_index, has_title_index, health, health_error, '
    'expected_size, scanned_at, created_at, updated_at'
)


def _probe(path: Path) -> dict:
    """Open one archive and read everything the registry stores about it.

    Never raises: an archive that cannot be opened is a row with
    `health='unreadable'`, because one corrupt file in a folder of six hundred
    must not be able to fail the scan that would have found the other 599.
    """
    try:
        stat = path.stat()
    except OSError as exc:
        return {'health': 'unreadable', 'health_error': str(exc), 'size': 0}

    row: dict = {'size': stat.st_size, 'health': 'ok', 'health_error': None}
    try:
        zim = _archive_mod._archive(path)
    except Exception as exc:  # libzim raises its own types; all mean the same here
        row.update(health='unreadable', health_error=str(exc))
        return row

    meta = {
        name: _archive_mod._metadata(zim, name)
        for name in ('Title', 'Language', 'Date', 'Flavour', 'Tags', 'Name', 'Creator')
    }
    kind, match_terms = kinds.classify(meta, path.name)
    has_ft = bool(_archive_mod._call_value(
        zim, 'has_fulltext_index', 'hasFulltextIndex', default=True))
    has_title = bool(_archive_mod._call_value(
        zim, 'has_title_index', 'hasTitleIndex', default=True))
    row.update(
        zim_uuid=str(_archive_mod._call_value(zim, 'uuid', default='') or ''),
        title=meta['Title'] or path.stem,
        language=meta['Language'],
        zim_date=meta['Date'],
        flavour=meta['Flavour'],
        article_count=_archive_mod._call_value(zim, 'article_count', 'getArticleCount'),
        kind=kind,
        match_terms=match_terms,
        has_fulltext_index=int(has_ft),
        has_title_index=int(has_title),
    )
    if not has_ft:
        # Not an error. Every DevDocs archive Kiwix publishes is `_ftindex:no`,
        # and the title index still answers -- see archive.search_many.
        row['health'] = 'no_fulltext'
    return row


def sync(*, force: bool = False) -> dict:
    """Reconcile the table against the configured roots. Returns a summary.

    An archive is only *opened* when it is new or its size changed, so the
    steady-state cost of a scan is the rglob plus one SELECT. `force` reopens
    everything, which is what the rescan route is for after a file is replaced
    in place at the same size.
    """
    db = get_db()
    now = int(time.time())
    root = _archive_mod.configured_root()
    if root is None or not root.is_dir():
        # An unconfigured or unmounted root is not the same event as an emptied
        # library, and the difference matters: the archive drive is external.
        # Marking every row `missing` because a mountpoint was not ready would
        # churn the whole table on every boot race.
        return {'added': 0, 'changed': 0, 'missing': 0, 'total': 0,
                'rootAvailable': False}
    existing = {
        r['id']: dict(r)
        for r in db.execute(f'SELECT {_COLUMNS} FROM knowledge_archives').fetchall()
    }
    by_uuid = {r['zim_uuid']: r for r in existing.values() if r['zim_uuid']}

    seen: set[str] = set()
    added = changed = 0
    for path in _archive_mod._paths():
        ident = _archive_mod.archive_id(path)
        prior = existing.get(ident)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if prior and not force and prior['size'] == size and prior['health'] != 'missing':
            seen.add(ident)
            db.execute(
                'UPDATE knowledge_archives SET scanned_at=?, updated_at=? WHERE id=?',
                (now, now, ident),
            )
            continue

        probed = _probe(path)
        # A renamed or moved file has a new id but the same ZIM uuid. Adopt the
        # old row's user-facing state rather than resurrecting it as a fresh,
        # default-on archive whose disabled flag the user has to set again.
        inherited = prior or by_uuid.get(probed.get('zim_uuid') or '\x00')
        if probed.get('expected_size') is None and inherited:
            probed['expected_size'] = inherited['expected_size']
        expected = probed.get('expected_size')
        if expected and probed['size'] < expected and probed['health'] != 'unreadable':
            probed['health'] = 'truncated'

        kind = probed.get('kind', 'other')
        kind_source = 'derived'
        if inherited and inherited['kind_source'] == 'user':
            kind, kind_source = inherited['kind'], 'user'

        db.execute(
            'INSERT INTO knowledge_archives (id, zim_uuid, path, filename, title, '
            ' language, zim_date, flavour, size, article_count, kind, kind_source, '
            ' match_terms, enabled, has_fulltext_index, has_title_index, health, '
            ' health_error, expected_size, scanned_at, created_at, updated_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(id) DO UPDATE SET '
            ' zim_uuid=excluded.zim_uuid, path=excluded.path, filename=excluded.filename, '
            ' title=excluded.title, language=excluded.language, zim_date=excluded.zim_date, '
            ' flavour=excluded.flavour, size=excluded.size, '
            ' article_count=excluded.article_count, kind=excluded.kind, '
            ' kind_source=excluded.kind_source, match_terms=excluded.match_terms, '
            ' has_fulltext_index=excluded.has_fulltext_index, '
            ' has_title_index=excluded.has_title_index, health=excluded.health, '
            ' health_error=excluded.health_error, expected_size=excluded.expected_size, '
            ' scanned_at=excluded.scanned_at, updated_at=excluded.updated_at',
            (
                ident, probed.get('zim_uuid', ''), str(path), path.name,
                probed.get('title', path.stem), probed.get('language', ''),
                probed.get('zim_date', ''), probed.get('flavour', ''),
                probed.get('size', 0), probed.get('article_count'),
                kind, kind_source, probed.get('match_terms', ''),
                int(inherited['enabled']) if inherited else 1,
                probed.get('has_fulltext_index', 1), probed.get('has_title_index', 1),
                probed['health'], probed.get('health_error'),
                probed.get('expected_size'), now,
                inherited['created_at'] if inherited else now, now,
            ),
        )
        seen.add(ident)
        if prior:
            changed += 1
        else:
            added += 1

    gone = [ident for ident in existing if ident not in seen]
    for ident in gone:
        # Kept, not deleted: an unplugged archive drive must not read as "the
        # user threw their library away and started a new one".
        db.execute(
            "UPDATE knowledge_archives SET health='missing', updated_at=? WHERE id=?",
            (now, ident),
        )
    db.commit()
    return {'added': added, 'changed': changed, 'missing': len(gone),
            'total': len(seen), 'rootAvailable': True}


def ensure_synced(*, ttl: int = SCAN_TTL_SECONDS) -> None:
    """Sync if the newest scan is older than `ttl`. Cheap enough to call often."""
    row = get_db().execute(
        'SELECT MAX(scanned_at) AS newest, COUNT(*) AS n FROM knowledge_archives'
    ).fetchone()
    newest = (row['newest'] if row else None) or 0
    if row and row['n'] and newest > time.time() - ttl:
        return
    try:
        sync()
    except _archive_mod.KnowledgeUnavailable:
        raise
    except Exception:
        # A scan failing must not take down the search that asked for it; the
        # rows from the previous scan are still usable.
        logger.exception('knowledge archive scan failed')


def rows(*, enabled_only: bool = True, kinds_wanted=None,
         healthy_only: bool = True) -> list[dict]:
    """Registry rows, newest scan first applied. Pure DB, no filesystem."""
    sql = f'SELECT {_COLUMNS} FROM knowledge_archives'
    where, params = [], []
    if enabled_only:
        where.append('enabled = 1')
    if healthy_only:
        # 'no_fulltext' is included on purpose: those archives are searchable
        # through the title index, which is the whole DevDocs collection.
        where.append("health IN ('ok','no_fulltext')")
    if kinds_wanted:
        where.append('kind IN (%s)' % ','.join('?' * len(kinds_wanted)))
        params.extend(kinds_wanted)
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY kind, filename'
    return [dict(r) for r in get_db().execute(sql, params).fetchall()]


def row(archive_id: str) -> dict | None:
    found = get_db().execute(
        f'SELECT {_COLUMNS} FROM knowledge_archives WHERE id=?', (archive_id,)
    ).fetchone()
    return dict(found) if found else None


def path_for(archive_id: str) -> Path | None:
    """The file behind an id -- one indexed SELECT, where `_resolve` rglobbed."""
    found = get_db().execute(
        'SELECT path FROM knowledge_archives WHERE id=?', (archive_id,)
    ).fetchone()
    return Path(found['path']) if found else None


def set_enabled(archive_id: str, enabled: bool) -> bool:
    db = get_db()
    cur = db.execute(
        'UPDATE knowledge_archives SET enabled=?, updated_at=? WHERE id=?',
        (1 if enabled else 0, int(time.time()), archive_id),
    )
    db.commit()
    return cur.rowcount > 0


def set_kind(archive_id: str, kind: str) -> bool:
    if kind not in kinds.KINDS:
        raise ValueError(f'Unknown archive kind: {kind}')
    db = get_db()
    cur = db.execute(
        "UPDATE knowledge_archives SET kind=?, kind_source='user', updated_at=? "
        'WHERE id=?',
        (kind, int(time.time()), archive_id),
    )
    db.commit()
    return cur.rowcount > 0
