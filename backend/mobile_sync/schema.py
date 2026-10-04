"""Install transaction-coupled change capture after the feature migrations."""
import time

from ulid import ULID

from .registry import COLLECTIONS, SCHEMA_HASH, included, payload


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
                condition += ' AND (' + ' OR '.join(f'OLD."{c}" IS NOT NEW."{c}"' for c in columns) + ')'
            value = 'NULL' if event == 'DELETE' else payload(table, 'NEW')
            db.execute(f'''
                CREATE TRIGGER IF NOT EXISTS mobile_sync_{table}_{event.lower()}
                AFTER {event} ON "{table}" WHEN {condition}
                BEGIN
                    INSERT INTO mobile_sync_changes(collection, record_id, payload)
                    VALUES ('{table}', {prefix}.id, {value});
                END
            ''')
        initial = payload(table, 'source')
        db.execute(f'''
            INSERT INTO mobile_sync_changes(collection, record_id, payload)
            SELECT ?, source.id, {initial} FROM "{table}" AS source
            WHERE {included(table, 'source')} AND NOT EXISTS (
                SELECT 1 FROM mobile_sync_changes AS c
                WHERE c.collection=? AND c.record_id=source.id
            )
        ''', (table, table))
    # These relationships are part of each immutable book snapshot, rather than
    # live joins that could change halfway through a bootstrap.
    for table in ('fic_folder_items', 'fic_site_tags', 'fic_chapters'):
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            prefixes = ('OLD', 'NEW') if event == 'UPDATE' else ('OLD',) if event == 'DELETE' else ('NEW',)
            ids = ','.join(f'{prefix}.fic_id' for prefix in prefixes)
            update = 'UPDATE OF fic_id,posted_at,created_at' if table == 'fic_chapters' and event == 'UPDATE' else event
            db.execute(f"""
                CREATE TRIGGER IF NOT EXISTS mobile_sync_book_meta_{table}_{event.lower()}
                AFTER {update} ON "{table}"
                BEGIN
                    INSERT INTO mobile_sync_changes(collection,record_id,payload)
                    SELECT 'fics',source.id,{payload('fics', 'source')} FROM fics source
                    WHERE source.id IN ({ids});
                END
            """)
    db.commit()
