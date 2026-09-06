"""Combine qBittorrent's live view with Lunaschal's own rows into one payload.

Pure and DB-free so it can be unit-tested without a client or a database, in the
spirit of backend/meetings/merge.py.

The split of responsibility is the important part. **qBittorrent owns every
fact about a torrent** — progress, speeds, category, share limits, where the
files are. Lunaschal's `torrents` row owns only what qBittorrent has no concept
of: the note you wrote, and how long to keep the download. Nothing is mirrored
between the two, so nothing can drift out of sync; if you want a torrent's ratio
limit you ask the client, every time.

That is also why this feature has no in-memory progress registry and no
`_reset_stale_*` migration, unlike fanfic downloads and meeting transcription.
Those track work *this process* is doing, so a restart orphans it. Here the work
happens in a container that outlives Flask entirely — there is no in-flight
state of ours to strand.
"""

# qBittorrent reports 8640000 (100 days) for "unknown/never", and it renders in
# a UI as a very confident 100 days.
_ETA_INFINITY = 8640000

# qBittorrent 5.0 renamed the paused* states to stopped*; both spellings appear
# depending on the image, so both are listed.
_STATE_GROUPS = {
    'error': 'error',
    'missingFiles': 'error',
    'unknown': 'error',
    'uploading': 'seeding',
    'forcedUP': 'seeding',
    'stalledUP': 'seeding',
    'pausedUP': 'complete',
    'stoppedUP': 'complete',
    'queuedUP': 'queued',
    'checkingUP': 'checking',
    'downloading': 'downloading',
    'forcedDL': 'downloading',
    'metaDL': 'downloading',
    'forcedMetaDL': 'downloading',
    'stalledDL': 'stalled',
    'pausedDL': 'paused',
    'stoppedDL': 'paused',
    'queuedDL': 'queued',
    'checkingDL': 'checking',
    'allocating': 'checking',
    'checkingResumeData': 'checking',
    'moving': 'checking',
}

# Groups where something is still expected to change on its own, so the UI
# should keep polling.
_LIVE_GROUPS = frozenset({'downloading', 'seeding', 'checking', 'queued', 'stalled'})


def state_group(state: str) -> str:
    return _STATE_GROUPS.get(state, 'error')


def is_live(state: str) -> bool:
    return state_group(state) in _LIVE_GROUPS


def _timestamp(value) -> int | None:
    """qBittorrent uses 0 and -1 interchangeably for "hasn't happened"."""
    return value if isinstance(value, int) and value > 0 else None


def _eta(value) -> int | None:
    if not isinstance(value, int) or value <= 0 or value >= _ETA_INFINITY:
        return None
    return value


def present(live: dict, row: dict | None = None) -> dict:
    """One qBittorrent torrent, plus our row for it if we have one."""
    state = live.get('state') or 'unknown'
    group = state_group(state)
    return {
        'id': (row or {}).get('id'),
        'infoHash': live.get('hash'),
        'name': live.get('name') or (row or {}).get('name') or '',
        'state': state,
        'stateGroup': group,
        'live': group in _LIVE_GROUPS,
        'progress': round(float(live.get('progress') or 0.0), 4),
        'size': live.get('size') or live.get('total_size') or 0,
        'downloaded': live.get('downloaded') or 0,
        'uploaded': live.get('uploaded') or 0,
        'ratio': round(float(live.get('ratio') or 0.0), 3),
        'dlSpeed': live.get('dlspeed') or 0,
        'upSpeed': live.get('upspeed') or 0,
        'eta': _eta(live.get('eta')),
        'numSeeds': live.get('num_seeds') or 0,
        'numLeechs': live.get('num_leechs') or 0,
        'category': live.get('category') or '',
        'savePath': live.get('save_path') or '',
        'contentPath': live.get('content_path') or '',
        'addedAt': _timestamp(live.get('added_on')),
        'completedAt': _timestamp(live.get('completion_on')),
        # -2 is qBittorrent's "use the global setting", -1 its "no limit".
        # Both mean "nothing specific is set here", which is one idea in a UI.
        'ratioLimit': _limit(live.get('ratio_limit')),
        'seedingMinutes': _limit(live.get('seeding_time_limit')),
        'dlLimit': live.get('dl_limit') or 0,
        'upLimit': live.get('up_limit') or 0,
        # Ours, and only ours.
        'note': (row or {}).get('note'),
        'retentionDays': (row or {}).get('retention_days'),
        # False for a torrent added straight in qBittorrent rather than through
        # Lunaschal. It still shows and still works — the tab is a view of the
        # client, not of our table — but it has no note and no retention.
        'tracked': row is not None,
    }


def _limit(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number < 0 else number


def merge(live_list: list[dict], rows: list[dict]) -> list[dict]:
    """Every torrent the client knows about, decorated with our row where one
    exists. Ordered newest-added first."""
    by_hash = {(r.get('info_hash') or '').lower(): r for r in rows}
    merged = [
        present(live, by_hash.get((live.get('hash') or '').lower()))
        for live in live_list
    ]
    merged.sort(key=lambda t: t['addedAt'] or 0, reverse=True)
    return merged


def orphaned_hashes(live_list: list[dict], rows: list[dict]) -> list[str]:
    """Our rows whose torrent is no longer in the client — removed from
    qBittorrent's own UI, or lost with its config. The reconcile pass deletes
    these; keeping them would show phantom entries that no action can affect."""
    live_hashes = {(t.get('hash') or '').lower() for t in live_list}
    return [
        r['info_hash']
        for r in rows
        if (r.get('info_hash') or '').lower() not in live_hashes
    ]
