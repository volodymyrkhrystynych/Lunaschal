from flask import Blueprint, jsonify, request
from backend.auth import require_auth
from backend.ai.embeddings import is_embeddings_configured
from backend.ai.rag import (
    sync_journal_embeddings, sync_all_journal_embeddings,
    search_for_context, get_embedding_stats,
)

bp = Blueprint('rag', __name__, url_prefix='/api/rag')


@bp.get('/configured')
@require_auth
def configured():
    return jsonify(is_embeddings_configured())


@bp.get('/stats')
@require_auth
def stats():
    return jsonify(get_embedding_stats())


@bp.post('/sync/<journal_id>')
@require_auth
def sync_journal(journal_id):
    chunks = sync_journal_embeddings(journal_id)
    return jsonify({'chunks': chunks})


@bp.post('/sync-all')
@require_auth
def sync_all():
    result = sync_all_journal_embeddings()
    return jsonify(result)


@bp.get('/search')
@require_auth
def search():
    query = request.args.get('query', '').strip()
    limit = min(int(request.args.get('limit', 5)), 20)
    if not query:
        return jsonify([])
    return jsonify(search_for_context(query, limit))
