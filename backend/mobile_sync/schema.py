"""Install transaction-coupled change capture after the feature migrations."""
import time

from ulid import ULID

from backend import authorship

from .registry import AUTHORED, COLLECTIONS, SCHEMA_HASH, included, key, payload


def ensure(db):
    columns = {r[1] for r in db.execute('PRAGMA table_info(mobile_sync_state)')}
    if 'history_floor' not in columns:
        db.execute('ALTER TABLE mobile_sync_state ADD COLUMN history_floor INTEGER NOT NULL DEFAULT 0')
    state = db.execute('SELECT * FROM mobile_sync_state').fetchone()
    if state is not None and state['schema_hash'] != SCHEMA_HASH:
        # A protocol projection changed: do not serve a mixture of old/new
        # payload shapes at one cursor. Existing operations stay deduplicated.
        db.execute('DELETE FROM mobile_sync_changes')
        db.execute('DELETE FROM mobile_sync_state')
        state = None
    if state is None:
        db.execute('INSERT INTO mobile_sync_state(id, schema_hash, created_at) VALUES (?,?,?)',
                   (str(ULID()), SCHEMA_HASH, int(time.time())))
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name GLOB 'mobile_sync_*'").fetchall():
            db.execute(f'DROP TRIGGER "{row[0]}"')
    for table, columns in COLLECTIONS.items():
        # Names/columns come only from the code allowlist, never requests.
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            prefix = 'OLD' if event == 'DELETE' else 'NEW'
            condition = '1' if event == 'DELETE' else included(table, prefix)
            if event == 'UPDATE':
                changed = [f'OLD."{c}" IS NOT NEW."{c}"' for c in columns]
                if table == 'fic_chapters':
                    # `has_source` is derived: a forced re-download that only
                    # fills `source_html` still changes what the phone shows.
                    changed.append('(OLD.source_html IS NULL) IS NOT (NEW.source_html IS NULL)')
                condition += ' AND (' + ' OR '.join(changed) + ')'
            value = 'NULL' if event == 'DELETE' else payload(table, 'NEW')
            db.execute(f'''
                CREATE TRIGGER IF NOT EXISTS mobile_sync_{table}_{event.lower()}
                AFTER {event} ON "{table}" WHEN {condition}
                BEGIN
                    INSERT INTO mobile_sync_changes(collection, record_id, payload)
                    VALUES ('{table}', {prefix}."{key(table)}", {value});
                END
            ''')
        initial = payload(table, 'source')
        db.execute(f'''
            INSERT INTO mobile_sync_changes(collection, record_id, payload)
            SELECT ?, source."{key(table)}", {initial} FROM "{table}" AS source
            WHERE {included(table, 'source')} AND NOT EXISTS (
                SELECT 1 FROM mobile_sync_changes AS c
                WHERE c.collection=? AND c.record_id=source."{key(table)}"
            )
        ''', (table, table))
    # These relationships are part of each immutable book snapshot, rather than
    # live joins that could change halfway through a bootstrap.
    for table in ('fic_folder_items', 'fic_site_tags', 'fic_chapters', 'fic_chapter_reads', 'fic_source_lists'):
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            prefixes = ('OLD', 'NEW') if event == 'UPDATE' else ('OLD',) if event == 'DELETE' else ('NEW',)
            ids = ','.join(f'{prefix}.fic_id' for prefix in prefixes)
            update = 'UPDATE OF fic_id,posted_at,created_at,category,source_html' if table == 'fic_chapters' and event == 'UPDATE' else event
            db.execute(f"""
                CREATE TRIGGER IF NOT EXISTS mobile_sync_book_meta_{table}_{event.lower()}
                AFTER {update} ON "{table}"
                BEGIN
                    INSERT INTO mobile_sync_changes(collection,record_id,payload)
                    SELECT 'fics',source.id,{payload('fics', 'source')} FROM fics source
                    WHERE source.id IN ({ids});
                END
            """)
    # Spans are part of the record's payload but live in their own table, so a
    # span change that moves no projected column (a model write landing after
    # the row's UPDATE) re-sends the record from here. Only spans that describe
    # the record's current text are sent: an edit is recorded just *before* its
    # UPDATE, and publishing then would put the old text with stale spans into
    # history as a revision of its own.
    for table in AUTHORED:
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            prefix = 'OLD' if event == 'DELETE' else 'NEW'
            field = ' '.join(f"WHEN '{f}' THEN COALESCE(source.\"{f}\", '')"
                             for f in authorship.FIELDS[table])
            in_effect = (f'{prefix}.text IS (SELECT CASE {prefix}.field {field} END'
                         f' FROM "{table}" source WHERE source.id={prefix}.row_id)')
            db.execute(f"""
                CREATE TRIGGER IF NOT EXISTS mobile_sync_authorship_{table}_{event.lower()}
                AFTER {event} ON text_authorship
                WHEN {prefix}.target='{table}' AND {in_effect}
                BEGIN
                    INSERT INTO mobile_sync_changes(collection,record_id,payload)
                    SELECT '{table}',source.id,{payload(table, 'source')} FROM "{table}" source
                    WHERE source.id={prefix}.row_id AND {included(table, 'source')};
                END
            """)
    # A site's first complete pass over a list that counts is what lets its
    # fics count as Backlog at all, and only some lists count (a forum's
    # bookmarks don't), so the first pass over each (site, list) re-sends the
    # site's snapshots. Later passes only move completed_at, which changes none.
    db.execute(f"""
        CREATE TRIGGER IF NOT EXISTS mobile_sync_book_meta_fic_list_passes_insert
        AFTER INSERT ON fic_list_passes
        BEGIN
            INSERT INTO mobile_sync_changes(collection,record_id,payload)
            SELECT 'fics',source.id,{payload('fics', 'source')} FROM fics source
            WHERE source.site=NEW.site;
        END
    """)
    db.commit()
