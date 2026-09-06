"""Torrent tab API.

Mostly a proxy onto qBittorrent, which owns all torrent state. The parts that
are genuinely ours: parsing pasted magnets, keeping a note and a retention
policy per torrent, refusing to add anything while the tunnel is down, and
serving a finished file back over Tailscale.

Failure shapes matter here and are deliberate: a stopped container is a 503 with
something actionable to do about it, an unreachable tunnel is a 409, and a bad
magnet is a per-item error inside a 200 so one bad line in a paste does not
discard the good ones.
"""

import os
import time

from flask import Blueprint, jsonify, request, send_file
from ulid import ULID

from backend.db.connection import build_update, get_db
from backend.torrent import client, merge, scheduler, storage, vpn
from backend.torrent.config import get_torrent_config
from backend.torrent.magnet import InvalidMagnet, parse_magnet, split_magnets
from backend.torrent.torrentfile import InvalidTorrentFile, info_hash_and_name

bp = Blueprint('torrent', __name__, url_prefix='/api/torrents')

_STACK_HINT = (
    'The torrent stack is not reachable. Start it with: '
    'systemctl --user start lunaschal-torrent'
)


def _unavailable(exc: Exception):
    return jsonify({'error': _STACK_HINT, 'detail': str(exc), 'available': False}), 503


def _rows() -> list[dict]:
    return [dict(r) for r in get_db().execute('SELECT * FROM torrents')]


def _row_for(info_hash: str) -> dict | None:
    row = get_db().execute(
        'SELECT * FROM torrents WHERE info_hash = ?', (info_hash.lower(),)
    ).fetchone()
    return dict(row) if row else None


# --- list / status ---------------------------------------------------------


@bp.get('')
@bp.get('/')
def list_torrents():
    """Everything the client knows about, plus the tunnel banner.

    The VPN reading rides along rather than living only at /vpn because the list
    polls every 1.5s while anything is active and the banner has to move with
    it; two endpoints would mean two round trips per tick for one screen.
    """
    try:
        live = client.torrents_info()
    except client.TorrentClientUnavailable as e:
        return _unavailable(e)
    except client.TorrentClientError as e:
        return jsonify({'error': str(e)}), 502
    return jsonify({'torrents': merge.merge(live, _rows()), 'vpn': vpn.status()})


@bp.get('/vpn')
def vpn_status():
    return jsonify(vpn.status())


@bp.get('/status')
def status_summary():
    """Small enough for the sidebar badge to poll without pulling the whole
    list — the badge has to work when the tab is closed."""
    tunnel = vpn.status()
    try:
        live = client.torrents_info()
    except client.TorrentClientError:
        return jsonify({
            'available': False,
            'vpnConnected': tunnel['connected'],
            'errored': 0,
            'active': 0,
        })
    groups = [merge.state_group(t.get('state') or '') for t in live]
    return jsonify({
        'available': True,
        'vpnConnected': tunnel['connected'],
        'errored': sum(1 for g in groups if g == 'error'),
        'active': sum(1 for g in groups if g in ('downloading', 'seeding')),
    })


# --- add -------------------------------------------------------------------


def _vpn_block() -> tuple | None:
    cfg = get_torrent_config()
    if not cfg['require_vpn']:
        return None
    tunnel = vpn.status(use_cache=False)
    if tunnel['connected']:
        return None
    return jsonify({
        'error': 'The ProtonVPN tunnel is not up, so nothing would connect. '
                 'Check the torrent stack, or turn off "require VPN" in Settings.',
        'vpn': tunnel,
    }), 409


def _insert(info_hash: str, name: str, source: str, magnet_uri: str | None,
            note: str | None, retention_days: int | None) -> str:
    now = int(time.time())
    torrent_id = str(ULID())
    get_db().execute(
        'INSERT INTO torrents (id, info_hash, name, source, magnet_uri, note,'
        ' retention_days, added_at, created_at, updated_at)'
        ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
        # Re-adding a torrent already in the client is a no-op there, so it must
        # be a no-op here too rather than a UNIQUE violation surfacing as a 500.
        ' ON CONFLICT(info_hash) DO UPDATE SET updated_at=excluded.updated_at',
        (torrent_id, info_hash, name, source, magnet_uri, note, retention_days, now, now, now),
    )
    return torrent_id


@bp.post('')
@bp.post('/')
def add_torrents():
    blocked = _vpn_block()
    if blocked:
        return blocked

    cfg = get_torrent_config()
    uploads = request.files.getlist('files')
    if uploads:
        payload = request.form
    else:
        payload = request.get_json(silent=True) or {}

    category = (payload.get('category') or '').strip()
    note = (payload.get('note') or '').strip() or None
    paused = str(payload.get('paused', '')).lower() in ('1', 'true', 'yes')
    # Three distinct answers, so membership is tested rather than truthiness:
    # the field being absent means "use the configured default", while a
    # cleared field means "keep forever" and must be able to override a
    # non-zero default. 0 and NULL both mean forever in the schema.
    if 'retentionDays' not in payload:
        retention_days = cfg['retention_days'] or None
    elif payload['retentionDays'] in (None, ''):
        retention_days = None
    else:
        try:
            retention_days = int(payload['retentionDays']) or None
        except (TypeError, ValueError):
            return jsonify({'error': 'Retention must be a whole number of days.'}), 400

    added: list[dict] = []
    errors: list[dict] = []

    # Magnets.
    raw = payload.get('magnets') or payload.get('magnet') or ''
    links = raw if isinstance(raw, list) else split_magnets(raw)
    parsed: list[tuple[str, str, str]] = []
    for link in links:
        try:
            info_hash, name = parse_magnet(link)
        except InvalidMagnet as e:
            # Per-item, inside a 200. Pasting ten links and losing all of them
            # to one typo is the behaviour this avoids.
            errors.append({'input': link[:120], 'error': str(e)})
            continue
        parsed.append((info_hash, name, link))

    if parsed:
        try:
            client.add_magnets([p[2] for p in parsed], category=category or None, paused=paused)
        except client.TorrentClientUnavailable as e:
            return _unavailable(e)
        except client.TorrentClientError as e:
            return jsonify({'error': str(e)}), 502
        for info_hash, name, link in parsed:
            _insert(info_hash, name, 'magnet', link, note, retention_days)
            added.append({'infoHash': info_hash, 'name': name})

    # .torrent uploads.
    files: list[tuple[str, bytes]] = []
    file_entries: list[tuple[str, str]] = []
    for upload in uploads:
        blob = upload.read()
        try:
            info_hash, name = info_hash_and_name(blob)
        except InvalidTorrentFile as e:
            errors.append({'input': upload.filename, 'error': str(e)})
            continue
        files.append((upload.filename or f'{info_hash}.torrent', blob))
        file_entries.append((info_hash, name or upload.filename or info_hash))

    if files:
        try:
            client.add_files(files, category=category or None, paused=paused)
        except client.TorrentClientUnavailable as e:
            return _unavailable(e)
        except client.TorrentClientError as e:
            return jsonify({'error': str(e)}), 502
        # Rows are written only once the client has accepted the files, so a
        # rejected upload never leaves a row for a torrent that does not exist.
        for info_hash, name in file_entries:
            _insert(info_hash, name, 'file', None, note, retention_days)
            added.append({'infoHash': info_hash, 'name': name})

    get_db().commit()

    if not added and errors:
        return jsonify({'added': [], 'errors': errors}), 400
    if not added and not errors:
        return jsonify({'error': 'Nothing to add.'}), 400
    return jsonify({'added': added, 'errors': errors}), 202


# --- per-torrent control ---------------------------------------------------


def _control(info_hash: str, action):
    try:
        action(info_hash)
    except client.TorrentClientUnavailable as e:
        return _unavailable(e)
    except client.TorrentClientError as e:
        return jsonify({'error': str(e)}), 404 if 'No such torrent' in str(e) else 502
    return jsonify({'ok': True})


@bp.post('/<info_hash>/pause')
def pause_torrent(info_hash):
    return _control(info_hash, client.pause)


@bp.post('/<info_hash>/resume')
def resume_torrent(info_hash):
    return _control(info_hash, client.resume)


@bp.post('/<info_hash>/recheck')
def recheck_torrent(info_hash):
    return _control(info_hash, client.recheck)


@bp.delete('/<info_hash>')
def delete_torrent(info_hash):
    delete_files = request.args.get('deleteFiles', '').lower() in ('1', 'true', 'yes')
    try:
        client.delete(info_hash, delete_files=delete_files)
    except client.TorrentClientUnavailable as e:
        return _unavailable(e)
    except client.TorrentClientError as e:
        return jsonify({'error': str(e)}), 502
    get_db().execute('DELETE FROM torrents WHERE info_hash = ?', (info_hash.lower(),))
    get_db().commit()
    return jsonify({'ok': True, 'deletedFiles': delete_files})


@bp.patch('/<info_hash>')
def update_torrent(info_hash):
    """Split write: limits and category go to the client, note and retention to
    our row. Nothing is written to both — see backend/torrent/merge.py."""
    body = request.get_json(silent=True) or {}

    try:
        if 'category' in body:
            client.set_category(info_hash, (body.get('category') or '').strip())
        if 'ratioLimit' in body or 'seedingMinutes' in body:
            client.set_share_limits(
                info_hash,
                ratio_limit=_optional_number(body.get('ratioLimit'), float),
                seeding_minutes=_optional_number(body.get('seedingMinutes'), int),
            )
        if 'dlLimit' in body:
            client.set_download_limit(info_hash, max(0, int(body.get('dlLimit') or 0)))
        if 'upLimit' in body:
            client.set_upload_limit(info_hash, max(0, int(body.get('upLimit') or 0)))
    except client.TorrentClientUnavailable as e:
        return _unavailable(e)
    except client.TorrentClientError as e:
        return jsonify({'error': str(e)}), 502
    except (TypeError, ValueError):
        return jsonify({'error': 'Limits must be numbers.'}), 400

    ours = {}
    if 'note' in body:
        ours['note'] = (body.get('note') or '').strip() or None
    if 'retentionDays' in body:
        raw = body.get('retentionDays')
        try:
            ours['retention_days'] = int(raw) if raw not in (None, '') else None
        except (TypeError, ValueError):
            return jsonify({'error': 'Retention must be a whole number of days.'}), 400

    if ours:
        row = _row_for(info_hash)
        if row is None:
            # A torrent added straight in qBittorrent has no row of ours yet.
            # Create one rather than refusing — the tab is a view of the client,
            # so anything in it should be annotatable.
            _insert(info_hash.lower(), body.get('name') or info_hash, 'magnet', None,
                    ours.get('note'), ours.get('retention_days'))
        else:
            ours['updated_at'] = int(time.time())
            build_update(get_db(), 'torrents', ours, 'info_hash=?', (info_hash.lower(),))
        get_db().commit()

    return jsonify({'ok': True})


def _optional_number(value, cast):
    """An emptied field means "fall back to the global limit", which the client
    spells -2. None carries that through set_share_limits."""
    if value in (None, ''):
        return None
    return cast(value)


# --- files -----------------------------------------------------------------


def _file_container_path(info_hash: str, index: int) -> str | None:
    info = client.torrents_info([info_hash])
    if not info:
        return None
    save_path = (info[0].get('save_path') or '').rstrip('/')
    files = client.torrent_files(info_hash)
    if index < 0 or index >= len(files):
        return None
    return f"{save_path}/{files[index].get('name') or ''}"


@bp.get('/<info_hash>/files')
def torrent_files(info_hash):
    try:
        files = client.torrent_files(info_hash)
    except client.TorrentClientUnavailable as e:
        return _unavailable(e)
    except client.TorrentClientError as e:
        return jsonify({'error': str(e)}), 404
    return jsonify([
        {
            'index': f.get('index', i),
            'name': f.get('name') or '',
            'size': f.get('size') or 0,
            'progress': round(float(f.get('progress') or 0.0), 4),
            'priority': f.get('priority', 1),
        }
        for i, f in enumerate(files)
    ])


@bp.get('/<info_hash>/files/<int:index>/download')
def download_file(info_hash, index):
    """Serve a downloaded file through Lunaschal.

    This is what makes a finished file reachable from the phone over Tailscale
    without exposing the client's own WebUI. `conditional=True` gives real range
    support, so a video seeks and streams rather than having to download whole.
    """
    try:
        container_path = _file_container_path(info_hash, index)
    except client.TorrentClientUnavailable as e:
        return _unavailable(e)
    except client.TorrentClientError as e:
        return jsonify({'error': str(e)}), 404
    if container_path is None:
        return jsonify({'error': 'Not found'}), 404

    # The boundary: everything above came from the client, which we do not get
    # to trust with a path that reaches send_file.
    path = storage.resolve_download_path(container_path)
    if path is None:
        return jsonify({'error': 'Refusing to serve a path outside the download root'}), 403
    if not path.is_file():
        # Normal for a file that has not finished yet.
        return jsonify({'error': 'Not downloaded yet'}), 404
    return send_file(path, conditional=True, download_name=os.path.basename(path))


# --- categories ------------------------------------------------------------


@bp.get('/categories')
def list_categories():
    try:
        return jsonify(sorted(client.categories().keys()))
    except client.TorrentClientUnavailable as e:
        return _unavailable(e)
    except client.TorrentClientError as e:
        return jsonify({'error': str(e)}), 502


@bp.post('/categories')
def create_category():
    name = ((request.get_json(silent=True) or {}).get('name') or '').strip()
    if not name:
        return jsonify({'error': 'A category needs a name.'}), 400
    try:
        client.create_category(name)
    except client.TorrentClientUnavailable as e:
        return _unavailable(e)
    except client.TorrentClientError as e:
        return jsonify({'error': str(e)}), 502
    return jsonify({'ok': True, 'name': name}), 201


@bp.delete('/categories/<name>')
def delete_category(name):
    try:
        client.remove_category(name)
    except client.TorrentClientUnavailable as e:
        return _unavailable(e)
    except client.TorrentClientError as e:
        return jsonify({'error': str(e)}), 502
    return jsonify({'ok': True})


# --- retention -------------------------------------------------------------


@bp.post('/purge')
def purge_now():
    """Run the retention sweep on demand. The scheduler does this daily in an
    08:00–09:00 window; this is the "don't wait until tomorrow" button."""
    try:
        return jsonify(scheduler.run_purge_sweep())
    except client.TorrentClientUnavailable as e:
        return _unavailable(e)
    except client.TorrentClientError as e:
        return jsonify({'error': str(e)}), 502
