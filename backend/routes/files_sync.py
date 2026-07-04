"""Manual git sync for the Files tab.

Status / init are fast and synchronous; the actual network sync (pull+push)
runs in a daemon thread with module-global state, mirroring the background-scan
pattern in curated_tags.py. Frontend polls /status while a sync is running.
"""
import threading
import time

from flask import Blueprint, jsonify

from backend import git_sync
from backend.db.connection import get_db
from backend.routes.files import FILES_ROOT
from backend.routes.settings import _get_settings

bp = Blueprint('files_sync', __name__, url_prefix='/api/files/sync')

_sync_state: dict = {
    'running': False,
    'phase': 'idle',
    'error': None,
    'conflicted': False,
    'finishedAt': None,
}
_sync_lock = threading.Lock()


def _branch() -> str:
    s = _get_settings()
    return (s.get('git_branch') if s else None) or git_sync.DEFAULT_BRANCH


def _build_status() -> dict:
    s = _get_settings() or {}
    with _sync_lock:
        run_state = dict(_sync_state)

    if not git_sync.is_repo(FILES_ROOT):
        return {
            'isRepo': False, 'hasRemote': False, 'remoteUrl': None,
            'branch': (s.get('git_branch') or git_sync.DEFAULT_BRANCH),
            'dirty': False, 'ahead': None, 'behind': None, 'hasUpstream': False,
            'conflicted': False, 'detached': False,
            'running': run_state['running'], 'phase': run_state['phase'],
            'error': run_state['error'], 'lastSync': s.get('git_last_sync'),
        }

    st = git_sync.status(FILES_ROOT)
    remote = git_sync.get_remote(FILES_ROOT)
    return {
        'isRepo': True,
        'hasRemote': remote is not None,
        'remoteUrl': remote,
        'branch': git_sync.current_branch(FILES_ROOT),
        'dirty': st['dirty'],
        'ahead': st['ahead'],
        'behind': st['behind'],
        'hasUpstream': st['has_upstream'],
        'conflicted': st['conflicted'],
        'detached': st['detached'],
        'running': run_state['running'],
        'phase': run_state['phase'],
        'error': run_state['error'],
        'lastSync': s.get('git_last_sync'),
    }


@bp.get('/status')
def get_status():
    return jsonify(_build_status())


@bp.post('/init')
def init_repo():
    """Set up the Files folder as a git repo.

    - Already a repo → just (re)point the remote.
    - Remote already has notes + local folder empty → clone (onboard this machine).
    - Otherwise → init locally and stage the current notes for the first push.
    """
    s = _get_settings() or {}
    remote = s.get('git_remote_url')
    if not remote:
        return jsonify({'error': 'Set a git remote URL in Settings first'}), 400
    branch = _branch()

    if git_sync.is_repo(FILES_ROOT):
        git_sync.set_remote(FILES_ROOT, remote)
        git_sync.write_gitignore(FILES_ROOT)
        return jsonify(_build_status())

    has_content = git_sync.remote_has_content(remote)
    if has_content is None:
        return jsonify({'error': f'Could not reach remote "{remote}" — check the URL and your SSH access'}), 502

    local_empty = not any(FILES_ROOT.iterdir()) if FILES_ROOT.exists() else True
    if has_content:
        if not local_empty:
            return jsonify({'error': 'Remote already has notes but this folder is not empty. '
                                     'Back up and empty the folder to clone, or point at an empty remote.'}), 409
        res = git_sync.clone(remote, FILES_ROOT, branch)
        if not res['ok']:
            return jsonify({'error': res['error']}), 502
        return jsonify(_build_status())

    # Fresh remote: init here and stage existing notes for the first Sync to push.
    git_sync.init(FILES_ROOT, branch)
    git_sync.set_remote(FILES_ROOT, remote)
    try:
        git_sync.commit_all(FILES_ROOT)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500
    return jsonify(_build_status())


def _run_sync(branch: str) -> None:
    result = git_sync.sync(FILES_ROOT, branch)
    with _sync_lock:
        _sync_state['running'] = False
        _sync_state['finishedAt'] = int(time.time())
        if result.get('ok'):
            _sync_state['phase'] = 'idle'
            _sync_state['error'] = None
            _sync_state['conflicted'] = False
        else:
            _sync_state['phase'] = result.get('phase', 'error')
            _sync_state['error'] = result.get('error')
            _sync_state['conflicted'] = bool(result.get('conflicted'))
    if result.get('ok'):
        db = get_db()
        db.execute('UPDATE settings SET git_last_sync=? WHERE id=1', (int(time.time()),))
        db.commit()


@bp.post('')
def start_sync():
    if not git_sync.is_repo(FILES_ROOT):
        return jsonify({'error': 'Files folder is not a git repository — initialize it in Settings'}), 400
    if git_sync.get_remote(FILES_ROOT) is None:
        return jsonify({'error': 'No git remote configured'}), 400
    with _sync_lock:
        if _sync_state['running']:
            return jsonify({'error': 'Sync already running'}), 409
        _sync_state.update({'running': True, 'phase': 'commit', 'error': None, 'conflicted': False})
    threading.Thread(target=_run_sync, args=(_branch(),), daemon=True).start()
    return jsonify({'running': True}), 202
