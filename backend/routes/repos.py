"""Repositories the Ideas agent reads.

A repo is registered by git URL and cloned into ./data/repos/<slug>/ — Luna owns
every checkout, so nothing here can touch a working tree the user is editing.
The clone itself runs on the research worker (backend/repos/job.py); these
handlers only ever queue it, because a first clone is minutes and an HTTP
request is not the place to wait for one.
"""
from flask import Blueprint, jsonify, request

from backend.repos import graph, job, registry, storage
from backend.repos.git import UnsafeRemote

bp = Blueprint('repos', __name__, url_prefix='/api/repos')


def _payload(repo: dict) -> dict:
    """A repo row plus the two facts that only the filesystem knows."""
    root = storage.repo_dir(repo['slug'])
    repo['hasCheckout'] = bool(root and (root / '.git').exists())
    repo['hasGraph'] = graph.has_graph(root)
    return repo


@bp.get('')
def list_repos():
    return jsonify([_payload(r) for r in registry.list_repos()])


@bp.get('/<repo_id>')
def get_repo(repo_id):
    repo = registry.get_repo(repo_id)
    if not repo:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_payload(repo))


@bp.post('')
def create_repo():
    data = request.get_json(silent=True) or {}
    try:
        repo = registry.create_repo(
            data.get('remoteUrl') or data.get('url') or '',
            data.get('name') or '',
            data.get('branch') or '',
        )
    except UnsafeRemote as e:
        return jsonify({'error': str(e)}), 400

    queued = job.submit_import(repo['id'])
    # The worker takes one job at a time. A repo that could not be queued stays
    # 'pending' and is picked up by the nightly sweep or a manual Pull — saying
    # so is better than leaving the user watching a spinner that will not move.
    return jsonify({**_payload(repo), 'queued': queued}), 201


@bp.post('/<repo_id>/pull')
def pull_repo(repo_id):
    repo = registry.get_repo(repo_id)
    if not repo:
        return jsonify({'error': 'Not found'}), 404
    root = storage.repo_dir(repo['slug'])
    # A repo whose checkout never landed needs the clone, not a fetch.
    queued = (
        job.submit_pull(repo_id) if root and (root / '.git').exists()
        else job.submit_import(repo_id)
    )
    if not queued:
        return jsonify({'error': 'The research worker is busy; try again shortly'}), 409
    return jsonify({'queued': True}), 202


@bp.post('/<repo_id>/default')
def make_default(repo_id):
    if not registry.get_repo(repo_id):
        return jsonify({'error': 'Not found'}), 404
    registry.set_default(repo_id)
    return jsonify({'success': True})


@bp.delete('/<repo_id>')
def delete_repo(repo_id):
    if not registry.delete_repo(repo_id):
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'success': True})
