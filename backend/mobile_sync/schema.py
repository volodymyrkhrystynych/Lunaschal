"""Install transaction-coupled change capture after the feature migrations."""
import time

from ulid import ULID

from .registry import COLLECTIONS, SCHEMA_HASH, included


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
        fields = ','.join(f"'{c}',NEW.\"{c}\"" for c in columns)
        payload = f'json_object({fields})'
        # Names/columns come only from the code allowlist, never requests.
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            prefix = 'OLD' if event == 'DELETE' else 'NEW'
            condition = '1' if event == 'DELETE' else included(table, prefix)
            if event == 'UPDATE':
                condition += ' AND (' + ' OR '.join(f'OLD."{c}" IS NOT NEW."{c}"' for c in columns) + ')'
            value = 'NULL' if event == 'DELETE' else payload
            db.execute(f'''
                CREATE TRIGGER IF NOT EXISTS mobile_sync_{table}_{event.lower()}
                AFTER {event} ON "{table}" WHEN {condition}
                BEGIN
                    INSERT INTO mobile_sync_changes(collection, record_id, payload)
                    VALUES ('{table}', {prefix}.id, {value});
                END
            ''')
        initial = 'json_object(' + ','.join(f"'{c}',source.\"{c}\"" for c in columns) + ')'
        db.execute(f'''
            INSERT INTO mobile_sync_changes(collection, record_id, payload)
            SELECT ?, source.id, {initial} FROM "{table}" AS source
            WHERE {included(table, 'source')} AND NOT EXISTS (
                SELECT 1 FROM mobile_sync_changes AS c
                WHERE c.collection=? AND c.record_id=source.id
            )
        ''', (table, table))
    db.commit()
