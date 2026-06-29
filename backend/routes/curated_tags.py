import sqlite3
import threading
import time

from flask import Blueprint, jsonify, request
from ulid import ULID

from backend.db.connection import get_db, row_to_dict
from backend.ai.journal import classify_entry_for_tag

bp = Blueprint('curated_tags', __name__, url_prefix='/api/curated-tags')

_scan_progress: dict[str, dict] = {}
_scan_lock = threading.Lock()


@bp.get('')
def list_tags():
    db = get_db()
    rows = db.execute(
        'SELECT ct.id, ct.name, ct.created_at, COUNT(jec.entry_id) AS entry_count'
        ' FROM curated_tags ct'
        ' LEFT JOIN journal_entry_curated_tags jec ON ct.id = jec.tag_id'
        ' GROUP BY ct.id'
        ' ORDER BY ct.created_at ASC'
    ).fetchall()
    result = [row_to_dict(r) for r in rows]
    with _scan_lock:
        for item in result:
            if item['id'] in _scan_progress:
                item['scanProgress'] = dict(_scan_progress[item['id']])
    return jsonify(result)


@bp.post('')
def create_tag():
    body = request.json or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    tag_id = str(ULID())
    now = int(time.time())
    try:
        db = get_db()
        db.execute(
            'INSERT INTO curated_tags(id, name, created_at) VALUES (?,?,?)',
            (tag_id, name, now),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Tag name already exists'}), 409
    _start_scan_bg(tag_id, name)
    return jsonify({'id': tag_id}), 201


@bp.patch('/<tag_id>')
def rename_tag(tag_id):
    body = request.json or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    try:
        db = get_db()
        db.execute('UPDATE curated_tags SET name=? WHERE id=?', (name, tag_id))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Tag name already exists'}), 409
    return jsonify({'success': True})


@bp.delete('/<tag_id>')
def delete_tag(tag_id):
    with _scan_lock:
        _scan_progress.pop(tag_id, None)
    db = get_db()
    db.execute('DELETE FROM curated_tags WHERE id=?', (tag_id,))
    db.commit()
    return jsonify({'success': True})


@bp.get('/<tag_id>/scan-status')
def scan_status(tag_id):
    with _scan_lock:
        progress = _scan_progress.get(tag_id)
    if not progress:
        return jsonify({'total': 0, 'processed': 0, 'done': True})
    return jsonify(dict(progress))


def _start_scan_bg(tag_id: str, tag_name: str) -> None:
    def _run():
        db = get_db()
        entry_ids = [r[0] for r in db.execute(
            'SELECT id FROM journal_entries ORDER BY created_at DESC'
        ).fetchall()]
        with _scan_lock:
            _scan_progress[tag_id] = {'total': len(entry_ids), 'processed': 0, 'done': False}
        for eid in entry_ids:
            with _scan_lock:
                if tag_id not in _scan_progress:
                    return  # tag deleted — abort
            row = db.execute('SELECT content FROM journal_entries WHERE id=?', (eid,)).fetchone()
            if row:
                try:
                    if classify_entry_for_tag(row['content'], tag_name):
                        db.execute(
                            'INSERT OR IGNORE INTO journal_entry_curated_tags(entry_id, tag_id) VALUES(?,?)',
                            (eid, tag_id),
                        )
                        db.commit()
                except Exception as e:
                    print(f'Tag scan error for entry {eid}: {e}')
            with _scan_lock:
                if tag_id in _scan_progress:
                    _scan_progress[tag_id]['processed'] += 1
        with _scan_lock:
            if tag_id in _scan_progress:
                _scan_progress[tag_id]['done'] = True
    threading.Thread(target=_run, daemon=True).start()
