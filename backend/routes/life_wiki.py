"""Reading and correcting what the assistant has worked out about the user.

The wiki had no HTTP surface at all before this — it only ever held notes about
code, written and read by agents. A synthesized wiki *about the user* that they
cannot read or correct would be a worse version of the problem the Settings
memory editor exists to solve, so these routes are not optional dressing on the
nightly pass; they are what makes it fair to run.

Three things the user can do that the pass cannot undo:

- **Lock an article.** `upsert_article` already raises `ArticleLocked` for an
  agent write to a locked one, so the prose becomes theirs. Facts keep accruing
  underneath it, which is what a later unlock or rebuild renders from.
- **Lock a fact.** The pass can never supersede it — the "frozen components"
  mitigation from the drift research.
- **Rebuild.** Throw away everything derived and re-extract from the journal
  entries, messages and meals the facts cite. The sources are never mutated by
  any of this, so the wiki can always be rebuilt from them; that is what makes
  it a derived cache rather than a record.
"""
import threading

from flask import Blueprint, jsonify, request

from backend.lifewiki import facts as facts_mod
from backend.lifewiki import job
from backend.research import wiki

bp = Blueprint('life_wiki', __name__, url_prefix='/api/life-wiki')

# One rebuild at a time, and only one in flight per process. It is several
# model calls on a local server that the chat shares; letting the button queue
# them up would be a way to make the assistant unusable by clicking twice.
_rebuilding: set[str] = set()
_lock = threading.Lock()


def _article_or_404(slug: str):
    article = wiki.get_article(slug, None, kind=wiki.LIFE_KIND)
    if article is None:
        return None, (jsonify({'error': 'Not found'}), 404)
    return article, None


@bp.get('')
def list_articles():
    return jsonify(wiki.list_articles(wiki.WIKI_INDEX_MAX, repo_id=None,
                                      kind=wiki.LIFE_KIND))


@bp.get('/<slug>')
def get_article(slug):
    article, error = _article_or_404(slug)
    if error:
        return error
    article['facts'] = facts_mod.live_facts(article['id'])
    article['rebuilding'] = slug in _rebuilding
    return jsonify(article)


@bp.put('/<slug>')
def update_article(slug):
    """The user's own edit. Recorded with `author='user'`, so the revision log
    distinguishes it from the pass's renders."""
    article, error = _article_or_404(slug)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    updated = wiki.upsert_article(
        slug,
        body.get('title') or article['title'],
        body.get('summary', article.get('summary') or ''),
        body.get('content', article.get('content') or ''),
        repo_id=None, kind=wiki.LIFE_KIND, author='user', note='edited by hand',
    )
    return jsonify(updated)


@bp.post('/<slug>/lock')
def set_locked(slug):
    article, error = _article_or_404(slug)
    if error:
        return error
    locked = bool((request.get_json(silent=True) or {}).get('locked'))
    from backend.db.connection import build_update, get_db
    db = get_db()
    build_update(db, 'wiki_articles', {'locked': 1 if locked else 0},
                 'id=?', (article['id'],))
    db.commit()
    return jsonify(wiki.get_article(slug, None, kind=wiki.LIFE_KIND))


@bp.get('/<slug>/revisions')
def revisions(slug):
    article, error = _article_or_404(slug)
    if error:
        return error
    return jsonify(wiki.revisions(article['id']))


@bp.post('/<slug>/rebuild')
def rebuild(slug):
    """Re-derive the article from the rows its facts cite.

    Answers 202 and runs on a thread: this is several model calls on a local
    server, and a synchronous request would hold the connection open for a
    minute or more. The client re-reads the article to see the result — the same
    poll-the-row recovery `backend/delegate/runs.py` uses rather than inventing a
    second progress channel for one button.
    """
    article, error = _article_or_404(slug)
    if error:
        return error

    with _lock:
        if _rebuilding:
            return jsonify({'error': 'A rebuild is already running'}), 409
        _rebuilding.add(slug)

    def _run():
        try:
            job.rebuild_article(slug)
        except Exception as e:
            print(f'Life-wiki rebuild of {slug} failed: {e}')
        finally:
            with _lock:
                _rebuilding.discard(slug)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'rebuilding': True}), 202


@bp.post('/facts/<fact_id>/lock')
def lock_fact(fact_id):
    """A fact the user has checked. The pass may never supersede it after this."""
    locked = bool((request.get_json(silent=True) or {}).get('locked'))
    if not facts_mod.set_locked(fact_id, locked):
        return jsonify({'error': 'Not found'}), 404
    return jsonify(facts_mod.get_fact(fact_id))


@bp.delete('/facts/<fact_id>')
def delete_fact(fact_id):
    """The user's delete, and the only one there is — the pass supersedes."""
    if not facts_mod.delete_fact(fact_id):
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'ok': True})
