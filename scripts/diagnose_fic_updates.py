#!/usr/bin/env python3
"""Read-only check for fics whose update check can never find new chapters.

Prints each forum fic's per-category row counts and the position gaps in them.
A category whose stored rows outnumber the site's threadmark count latches the
old `cat.count <= stats['n']` short-circuit permanently: it is skipped before
any reader page is fetched, so new chapters are never looked for.

This can't see the site's counts (no network), so it reports the local shape —
compare a suspect category against the "Statistics (N threadmarks)" line on the
thread's /threadmarks page. Stored > site means that fic was stuck.

    python scripts/diagnose_fic_updates.py [path/to/lunaschal.db]
"""
import sqlite3
import sys


def main(db_path: str) -> None:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    fics = db.execute(
        'SELECT id, title, chapter_count, last_checked_at FROM fics'
        " WHERE source_type='xenforo' ORDER BY chapter_count DESC").fetchall()
    if not fics:
        print('No forum fics in this database.')
        return

    print(f'{len(fics)} forum fic(s)\n')
    for fic in fics:
        cats = db.execute(
            'SELECT category, COUNT(*) AS n, MIN(position) AS lo, MAX(position) AS hi,'
            ' SUM(source_post_id IS NULL) AS no_post_id'
            ' FROM fic_chapters WHERE fic_id=? GROUP BY category ORDER BY n DESC',
            (fic['id'],)).fetchall()
        print(f"{fic['title'][:70]}  ({fic['chapter_count']} chapters)")
        for c in cats:
            # positions are assigned per category walk; a span wider than the
            # row count means chapters were removed or never landed
            span = (c['hi'] or 0) - (c['lo'] or 0) + 1
            flags = []
            if span != c['n']:
                flags.append(f'GAP: positions {c["lo"]}-{c["hi"]} hold only {c["n"]} rows')
            if c['no_post_id']:
                flags.append(f'{c["no_post_id"]} row(s) with no source_post_id')
            note = ('  <-- ' + '; '.join(flags)) if flags else ''
            print(f"    {c['n']:>5} rows  category={c['category']!r}{note}")

        dupes = db.execute(
            'SELECT source_post_id, COUNT(*) AS n FROM fic_chapters'
            ' WHERE fic_id=? AND source_post_id IS NOT NULL'
            ' GROUP BY source_post_id HAVING n > 1', (fic['id'],)).fetchall()
        if dupes:
            print(f'    !! {len(dupes)} duplicated post id(s)')
        print()

    print('Compare each category\'s row count against that tab\'s'
          ' "Statistics (N threadmarks)" on the site.')
    print('Stored > site  =>  that fic was latched and could never see new chapters.')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'data/lunaschal.db')
