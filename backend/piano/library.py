"""The small, local practice library.

Archive files live on the removable backup drive. A favorite MusicXML score is
normalized and copied here so practice does not disappear when that drive is
unplugged. Keeping this write in one helper also makes manual imports and
archive promotion follow exactly the same validation and cleanup rules.
"""

import time

from ulid import ULID

from backend.db.connection import get_db
from backend.piano import storage
from backend.piano.musicxml import score_metadata


def store_piece(
    score: bytes,
    source_filename: str,
    *,
    db=None,
    commit: bool = True,
) -> tuple[str, object]:
    fallback = source_filename.rsplit('.', 1)[0].strip() or 'Untitled score'
    title, composer = score_metadata(score, fallback)
    piece_id = str(ULID())
    directory = storage.piano_dir(piece_id)
    assert directory is not None
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / 'score.musicxml'
    connection = db or get_db()
    try:
        path.write_bytes(score)
        now = int(time.time())
        connection.execute(
            'INSERT INTO piano_pieces '
            '(id,title,composer,source_filename,score_path,created_at,updated_at) '
            'VALUES (?,?,?,?,?,?,?)',
            (piece_id, title, composer, source_filename, str(path), now, now),
        )
        if commit:
            connection.commit()
    except Exception:
        storage.delete_piano_dir(piece_id)
        raise
    row = connection.execute(
        'SELECT * FROM piano_pieces WHERE id=?', (piece_id,)
    ).fetchone()
    return piece_id, row


def delete_piece(
    piece_id: str,
    *,
    db=None,
    commit: bool = True,
    delete_files: bool = True,
) -> bool:
    connection = db or get_db()
    cursor = connection.execute('DELETE FROM piano_pieces WHERE id=?', (piece_id,))
    if commit:
        connection.commit()
    if cursor.rowcount and delete_files:
        storage.delete_piano_dir(piece_id)
    return bool(cursor.rowcount)
