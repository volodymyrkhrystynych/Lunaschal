"""Which of the user's own lists on a site each fic is on, and the Backlog
derived from that.

A list is XenForo's watched threads or bookmarks, or one collection a
collection scan walks (FF.net favorites/follows, AO3 bookmarks/subscriptions,
the Patreon feed). Every scan marks what it sees as it goes; only a pass that
walked the list from its first page to its last without an error may take a fic
*off* it, by dropping marks older than the moment that pass began. A failed or
half-finished pass therefore never empties a list.

**Backlog** is an online fic the user doesn't follow on its site -- not
watched, followed, favourited or subscribed to; imported once from a link, an
alert or a search, or bookmarked to get around to. Forum bookmarks don't
count as following (see `_follows`). It only counts once that site has had at
least one complete pass over a list that does: before the first
watched-threads walk finishes, "on no list" means "not looked yet", and every
forum fic would land in the Backlog for no reason.

Callers own the transaction; nothing here commits.
"""
import time

ONLINE_SOURCE_TYPES = ('xenforo', 'fanfiction', 'ao3', 'patreon')


def mark(db, fic_id: str, list_name: str, now: int | None = None) -> None:
    db.execute(
        'INSERT INTO fic_source_lists(fic_id, list, seen_at) VALUES (?,?,?)'
        ' ON CONFLICT(fic_id, list) DO UPDATE SET seen_at=excluded.seen_at',
        (fic_id, list_name, now or int(time.time())))


def complete_pass(db, site: str, list_name: str, started_at: int | None) -> None:
    """A pass over `list_name` on `site` reached its last page. A pass with no
    recorded start (one begun before this bookkeeping existed) cannot say what
    it missed, so it neither prunes nor counts as complete."""
    if started_at is None:
        return
    db.execute(
        'DELETE FROM fic_source_lists WHERE list=? AND seen_at<?'
        ' AND fic_id IN (SELECT id FROM fics WHERE site=?)',
        (list_name, started_at, site))
    db.execute(
        'INSERT INTO fic_list_passes(site, list, completed_at) VALUES (?,?,?)'
        ' ON CONFLICT(site, list) DO UPDATE SET completed_at=excluded.completed_at',
        (site, list_name, int(time.time())))


def _follows(prefix: str, alias: str) -> str:
    """SQL: the list row `alias` is one that says the user follows the fic.

    A forum bookmark is filing, not following -- its labels become folders,
    and a "backlog" label is exactly the kind of thing it gets used for -- so
    it neither keeps a fic out of the Backlog nor, by finishing a pass,
    vouches for the watched list that does. AO3's bookmarks are its
    favourites and still count."""
    return f"NOT ({alias}.list='bookmarks' AND {prefix}.source_type='xenforo')"


def backlog_sql(prefix: str) -> str:
    """SQL truth value: the fic aliased `prefix` is in the Backlog."""
    types = ','.join(f"'{t}'" for t in ONLINE_SOURCE_TYPES)
    return (f'({prefix}.source_type IN ({types})'
            f' AND NOT EXISTS (SELECT 1 FROM fic_source_lists l'
            f' WHERE l.fic_id={prefix}.id AND {_follows(prefix, "l")})'
            f' AND EXISTS (SELECT 1 FROM fic_list_passes p'
            f' WHERE p.site={prefix}.site AND {_follows(prefix, "p")}))')


def lists_for(db, fic_ids: list[str]) -> dict[str, list[str]]:
    if not fic_ids:
        return {}
    marks = ','.join('?' * len(fic_ids))
    found: dict[str, list[str]] = {}
    for r in db.execute(f'SELECT fic_id, list FROM fic_source_lists WHERE fic_id IN ({marks})'
                        ' ORDER BY list', fic_ids):
        found.setdefault(r['fic_id'], []).append(r['list'])
    return found
