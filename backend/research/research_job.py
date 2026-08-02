"""What the background research worker actually runs, and what is due next.

`plan_next` is the whole scheduling policy in one readable function, and it
reads only from the DB — so the loop that calls it stays trivial and the policy
stays testable without threads.

Rule for everything here: never hold a transaction across a model or tool call.
`get_db()` hands out one process-global connection, so a `commit()` in any
Flask request handler would commit whatever this thread had pending. Write,
commit, then call the model.
"""
import logging
import time

from backend.db.connection import get_db, row_to_dict

logger = logging.getLogger(__name__)

# Statuses whose ideas are still worth spending model time on. Shipped and
# parked ones are settled.
ACTIVE_STATUSES = ('new', 'researching', 'ready', 'planned', 'building')

# Don't re-research the same idea more than once a day. Without this, a small
# backlog that is fully assessed would research its newest idea every tick
# forever.
RESEARCH_COOLDOWN_SECONDS = 24 * 3600


def _active_ideas(db) -> list[dict]:
    placeholders = ','.join('?' * len(ACTIVE_STATUSES))
    rows = db.execute(
        f'SELECT * FROM ideas WHERE status IN ({placeholders})'
        " AND (research_state IS NULL OR research_state = 'idle')"
        ' ORDER BY updated_at DESC',
        ACTIVE_STATUSES,
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def _needs_assessment(db, idea: dict, snapshot_id: str | None) -> bool:
    if not idea.get('assessmentId'):
        return True
    row = db.execute(
        'SELECT snapshot_id, assessed_at FROM idea_assessments WHERE id=?',
        (idea['assessmentId'],),
    ).fetchone()
    if not row:
        return True
    # The repo moved since the verdict was formed, so "already built" may have
    # changed underneath it.
    if snapshot_id and row['snapshot_id'] != snapshot_id:
        return True
    # The user edited the idea after it was judged.
    raw_updated = db.execute(
        'SELECT updated_at FROM ideas WHERE id=?', (idea['id'],)
    ).fetchone()['updated_at']
    return raw_updated > (row['assessed_at'] or 0)


def plan_next(now: int | None = None) -> tuple[str, str] | None:
    """(kind, idea_id) for the next task, or None when nothing is due.

    Assessment comes before research, always: it is cheap, needs no web access,
    and its output is what tells the research pass what to look for.
    """
    from backend.research.repo_job import current_snapshot
    from backend.research.web import is_search_configured

    now = now or int(time.time())
    db = get_db()
    ideas = _active_ideas(db)
    if not ideas:
        return None

    snapshot = current_snapshot()
    snapshot_id = (snapshot or {}).get('id')
    # With no repo snapshot there is nothing to judge an idea against, and
    # assessing anyway would only produce a row saying so.
    if snapshot_id:
        for idea in ideas:
            if _needs_assessment(db, idea, snapshot_id):
                return ('assess', idea['id'])

    # Researching without a search provider would just record that the web was
    # unavailable, over and over.
    if not is_search_configured():
        return None

    for idea in ideas:
        row = db.execute(
            'SELECT researched_at FROM ideas WHERE id=?', (idea['id'],)
        ).fetchone()
        last = row['researched_at'] or 0
        if now - last >= RESEARCH_COOLDOWN_SECONDS:
            return ('research', idea['id'])
    return None


def _set_state(idea_id: str, state: str, error: str | None = None) -> None:
    db = get_db()
    db.execute(
        'UPDATE ideas SET research_state=?, research_error=? WHERE id=?',
        (state, error, idea_id),
    )
    db.commit()


def run_assess_task(idea_id: str, cancel=None) -> None:
    """Assess one idea in the background."""
    from backend.research.agent import make_checkpoint
    from backend.research.assess import run_assessment

    _set_state(idea_id, 'running')
    try:
        make_checkpoint(cancel=cancel)()
        run_assessment(idea_id)
        _set_state(idea_id, 'idle')
    except Exception:
        _set_state(idea_id, 'idle', 'Assessment failed')
        raise


def run_research_task(idea_id: str, cancel=None) -> dict:
    """Research one idea and fold what was found into the wiki.

    Returns {articles, sources, steps} for logging and tests.
    """
    from backend.ai.idea_research import (
        GATHER_SYSTEM, build_gather_request, decide_articles, flatten_transcript,
    )
    from backend.research import agent, discuss, wiki

    db = get_db()
    row = db.execute('SELECT * FROM ideas WHERE id=?', (idea_id,)).fetchone()
    if not row:
        return {'articles': [], 'sources': [], 'steps': []}
    idea = row_to_dict(row)

    _set_state(idea_id, 'running')
    checkpoint = agent.make_checkpoint(cancel=cancel)
    try:
        result = agent.gather(
            GATHER_SYSTEM,
            build_gather_request(idea, discuss.build_context(idea_id)),
            checkpoint=checkpoint,
        )
        transcript = flatten_transcript(result.get('messages') or [])

        checkpoint()
        existing = wiki.list_articles()
        articles = decide_articles(idea, transcript, existing)

        written = []
        for item in articles:
            try:
                article = wiki.upsert_article(
                    item['slug'], item['title'], item.get('summary', ''),
                    item['content'],
                    sources=result.get('sources') or None,
                    note=item.get('note'),
                )
            except wiki.ArticleLocked:
                # The user owns this one. Skipping is the whole point of the flag.
                logger.info('Skipped locked wiki article %s', item['slug'])
                continue
            wiki.link_idea(idea_id, article['id'])
            written.append(article['slug'])

        now = int(time.time())
        db.execute('UPDATE ideas SET researched_at=? WHERE id=?', (now, idea_id))
        db.commit()
        _set_state(idea_id, 'idle')
        return {
            'articles': written,
            'sources': result.get('sources') or [],
            'steps': result.get('steps') or [],
        }
    except Exception:
        # researched_at stays unset so the idea is retried, but the state must
        # not be left 'running' or the planner will skip it forever.
        _set_state(idea_id, 'idle', 'Research failed')
        raise


def run_task(kind: str, idea_id: str, cancel=None):
    if kind == 'assess':
        return run_assess_task(idea_id, cancel)
    if kind == 'research':
        return run_research_task(idea_id, cancel)
    raise ValueError(f'Unknown research task: {kind}')
