"""Widen the Library source CHECK while preserving all reader relationships."""

from pathlib import Path


def ensure_fic_sources(db):
    ddl = db.execute("SELECT sql FROM sqlite_master WHERE name='fics'").fetchone()[0]
    if "'patreon'" in ddl:
        return
    schema = (Path(__file__).parent / 'schema.sql').read_text()
    table = schema.split('CREATE TABLE IF NOT EXISTS fics (', 1)[1].split('\n);', 1)[0]
    columns = ','.join('"' + r[1] + '"' for r in db.execute('PRAGMA table_info(fics)'))
    indexes = [r[0] for r in db.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name='fics' AND type IN ('index','trigger') AND sql IS NOT NULL")]
    db.commit()
    db.execute('PRAGMA foreign_keys=OFF')
    legacy = db.execute('PRAGMA legacy_alter_table').fetchone()[0]
    db.execute('PRAGMA legacy_alter_table=ON')
    try:
        db.execute('BEGIN')
        db.execute('CREATE TABLE fics_new (' + table + '\n)')
        db.execute(f'INSERT INTO fics_new ({columns}) SELECT {columns} FROM fics')
        db.execute('DROP TABLE fics')
        db.execute('ALTER TABLE fics_new RENAME TO fics')
        for sql in indexes:
            db.execute(sql)
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Library source migration failed its foreign key check')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute(f'PRAGMA legacy_alter_table={legacy}')
        db.execute('PRAGMA foreign_keys=ON')
