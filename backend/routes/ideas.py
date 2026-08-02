"""Ideas: the app's own feature backlog.

An idea is captured by voice or typing, kept as raw_content (never overwritten)
alongside AI-cleaned content, and developed against the repo-context snapshot
and the research wiki. Sketches are Paper *pages* borrowed into an idea; the
caption, not the image, is what the agent reads — see idea_sketches in
backend/db/schema.sql.
"""
import json
import time

from flask import Blueprint, Response, jsonify, request, stream_with_context
from ulid import ULID

from backend.ai.provider import is_ai_configured
from backend.db.connection import build_update, get_db, row_to_dict
from backend.routes.paper import page_image_url
from backend.tags import tags_json

bp = Blueprint('ideas', __name__, url_prefix='/api/ideas')

STATUSES = ('new', 'researching', 'ready', 'planned', 'building', 'shipped', 'parked')

# Columns the list endpoint returns: everything except the two body columns,
# which are only needed once an idea is opened.
_LIST_COLUMNS = (
    'id, title, status, tags, user_verdict, research_state, created_at, updated_at'
)


# --- Ideas ---

@bp.get('')
def list_ideas():
    db = get_db()
    rows = db.execute(
        f'SELECT {_LIST_COLUMNS} FROM ideas ORDER BY updated_at DESC'
    ).fetchall()
    # One grouped query per stat rather than a correlated subquery per idea —
    # the list is small, but N+1 here would be four queries per row.
    def _counts(sql):
        return {r['idea_id']: r['n'] for r in db.execute(sql).fetchall()}

    sketches = _counts('SELECT idea_id, COUNT(*) AS n FROM idea_sketches GROUP BY idea_id')
    questions = _counts(
        "SELECT idea_id, COUNT(*) AS n FROM idea_questions WHERE status='open'"
        ' GROUP BY idea_id'
    )
    articles = _counts(
        'SELECT idea_id, COUNT(*) AS n FROM idea_wiki_links GROUP BY idea_id'
    )
    plans = _counts('SELECT idea_id, COUNT(*) AS n FROM idea_plans GROUP BY idea_id')
    assessments = {
        r['idea_id']: r
        for r in db.execute(
            'SELECT a.* FROM idea_assessments a'
            ' JOIN ideas i ON i.assessment_id = a.id'
        ).fetchall()
    }

    from backend.research.repo_job import current_snapshot
    snapshot = current_snapshot()

    result = []
    for r in rows:
        d = row_to_dict(r)
        d['sketchCount'] = sketches.get(r['id'], 0)
        d['openQuestionCount'] = questions.get(r['id'], 0)
        d['articleCount'] = articles.get(r['id'], 0)
        d['hasPlan'] = plans.get(r['id'], 0) > 0

        assessment = assessments.get(r['id'])
        d['verdict'] = assessment['verdict'] if assessment else None
        d['confidence'] = assessment['confidence'] if assessment else None
        d['effort'] = assessment['effort'] if assessment else None
        d['onRoadmap'] = bool(assessment['on_roadmap']) if assessment else False
        # Stale means the repo moved since the verdict was formed — the honest
        # version of "already implemented".
        d['assessmentStale'] = bool(
            assessment and snapshot and assessment['snapshot_id'] != snapshot['id']
        )
        result.append(d)
    return jsonify(result)


@bp.post('')
def create_idea():
    body = request.json or {}
    title = (body.get('title') or '').strip()
    raw_content = body.get('rawContent') or ''
    if not title and not raw_content.strip():
        return jsonify({'error': 'title or rawContent required'}), 400
    now = int(time.time())
    id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO ideas(id, title, raw_content, content, status, tags, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?,?,?)',
        (id, title, raw_content, '', 'new', tags_json(body.get('tags')), now, now),
    )
    db.commit()
    return jsonify({'id': id}), 201


@bp.get('/<idea_id>')
def get_idea(idea_id):
    row = get_db().execute('SELECT * FROM ideas WHERE id=?', (idea_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


@bp.patch('/<idea_id>')
def update_idea(idea_id):
    body = request.json or {}
    updates: dict = {'updated_at': int(time.time())}
    if 'title' in body:
        updates['title'] = (body['title'] or '').strip()
    if 'rawContent' in body:
        updates['raw_content'] = body['rawContent']
    if 'content' in body:
        updates['content'] = body['content']
    if 'status' in body:
        if body['status'] not in STATUSES:
            return jsonify({'error': 'invalid status'}), 400
        updates['status'] = body['status']
    if 'tags' in body:
        updates['tags'] = tags_json(body['tags'])
    if 'userVerdict' in body:
        # The user's own call always beats the agent's, and it is stored
        # separately so a later assessment can't quietly overwrite it.
        verdict = body['userVerdict']
        if verdict not in (None, '', 'no', 'partial', 'yes'):
            return jsonify({'error': 'invalid userVerdict'}), 400
        updates['user_verdict'] = verdict or None
    if 'userVerdictNote' in body:
        updates['user_verdict_note'] = (body['userVerdictNote'] or '').strip() or None
    db = get_db()
    build_update(db, 'ideas', updates, 'id=?', (idea_id,))
    db.commit()
    return jsonify({'success': True})


@bp.delete('/<idea_id>')
def delete_idea(idea_id):
    db = get_db()
    db.execute('DELETE FROM ideas WHERE id=?', (idea_id,))
    db.commit()
    return jsonify({'success': True})


@bp.post('/voice')
def create_from_voice():
    """Save a transcript as a new idea, mirroring the journal's voice path: the
    transcript lands in raw_content untouched and titling/cleanup happens later."""
    body = request.json or {}
    raw_content = (body.get('rawContent') or '').strip()
    if not raw_content:
        return jsonify({'error': 'rawContent required'}), 400
    now = int(time.time())
    id = str(ULID())
    db = get_db()
    db.execute(
        'INSERT INTO ideas(id, title, raw_content, content, status, created_at, updated_at)'
        ' VALUES (?,?,?,?,?,?,?)',
        (id, '', raw_content, '', 'new', now, now),
    )
    db.commit()
    return jsonify({'id': id}), 201


# --- Sketches (Paper pages borrowed into an idea) ---

@bp.get('/<idea_id>/sketches')
def list_sketches(idea_id):
    db = get_db()
    rows = db.execute(
        'SELECT s.*, p.image_path, p.updated_at AS page_updated_at, p.paper_id'
        ' FROM idea_sketches s JOIN paper_pages p ON p.id = s.page_id'
        ' WHERE s.idea_id=? ORDER BY s.position ASC',
        (idea_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = row_to_dict(r)
        # Drop the joined page internals; the client only needs the image URL.
        d.pop('imagePath', None)
        d.pop('pageUpdatedAt', None)
        d['imageUrl'] = page_image_url(
            {'id': r['page_id'], 'image_path': r['image_path'],
             'updated_at': r['page_updated_at']}
        )
        result.append(d)
    return jsonify(result)


@bp.post('/<idea_id>/sketches')
def add_sketch(idea_id):
    body = request.json or {}
    page_id = (body.get('pageId') or '').strip()
    if not page_id:
        return jsonify({'error': 'pageId required'}), 400
    db = get_db()
    if not db.execute('SELECT 1 FROM ideas WHERE id=?', (idea_id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    if not db.execute('SELECT 1 FROM paper_pages WHERE id=?', (page_id,)).fetchone():
        return jsonify({'error': 'page not found'}), 404
    now = int(time.time())
    position = db.execute(
        'SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM idea_sketches WHERE idea_id=?',
        (idea_id,),
    ).fetchone()['next_pos']
    id = str(ULID())
    db.execute(
        'INSERT INTO idea_sketches(id, idea_id, page_id, caption, position, created_at)'
        ' VALUES (?,?,?,?,?,?)',
        (id, idea_id, page_id, body.get('caption') or '', position, now),
    )
    db.execute('UPDATE ideas SET updated_at=? WHERE id=?', (now, idea_id))
    db.commit()
    return jsonify({'id': id}), 201


@bp.patch('/sketches/<sketch_id>')
def update_sketch(sketch_id):
    body = request.json or {}
    updates: dict = {}
    if 'caption' in body:
        updates['caption'] = body['caption'] or ''
    if 'position' in body:
        updates['position'] = int(body['position'])
    if not updates:
        return jsonify({'success': True})
    db = get_db()
    build_update(db, 'idea_sketches', updates, 'id=?', (sketch_id,))
    db.commit()
    return jsonify({'success': True})


@bp.delete('/sketches/<sketch_id>')
def delete_sketch(sketch_id):
    db = get_db()
    db.execute('DELETE FROM idea_sketches WHERE id=?', (sketch_id,))
    db.commit()
    return jsonify({'success': True})


# --- Assessment, questions, research ---

@bp.post('/<idea_id>/assess')
def assess_idea_route(idea_id):
    """Judge the idea against the current repo snapshot. Synchronous: it is one
    grammar-constrained call, and the user is watching."""
    from backend.ai import priority
    from backend.research.assess import run_assessment

    if not is_ai_configured():
        return jsonify({'error': 'AI provider not configured'}), 400
    with priority.interactive('ideas.assess'):
        result = run_assessment(idea_id)
    if result is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(_assessment_payload(result)), 201


@bp.get('/<idea_id>/questions')
def list_questions(idea_id):
    rows = get_db().execute(
        'SELECT * FROM idea_questions WHERE idea_id=? ORDER BY status, created_at',
        (idea_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = row_to_dict(r)
        d['options'] = json.loads(r['options']) if r['options'] else []
        out.append(d)
    return jsonify(out)


@bp.patch('/questions/<question_id>')
def update_question(question_id):
    body = request.json or {}
    now = int(time.time())
    updates: dict = {'updated_at': now}
    if 'answer' in body:
        answer = (body['answer'] or '').strip()
        updates['answer'] = answer or None
        # Answering is what settles a question; clearing the answer reopens it.
        updates['status'] = 'answered' if answer else 'open'
        updates['answered_at'] = now if answer else None
    if 'status' in body:
        if body['status'] not in ('open', 'answered', 'dismissed'):
            return jsonify({'error': 'invalid status'}), 400
        updates['status'] = body['status']
    db = get_db()
    build_update(db, 'idea_questions', updates, 'id=?', (question_id,))
    db.commit()
    return jsonify({'success': True})


def _assessment_payload(assessment: dict) -> dict:
    from backend.research.assess import is_stale
    from backend.research.repo_job import current_snapshot

    payload = dict(assessment)
    payload['evidence'] = json.loads(assessment.get('evidence') or '[]')
    payload['onRoadmap'] = json.loads(assessment['onRoadmap']) if assessment.get('onRoadmap') else []
    payload['stale'] = is_stale(assessment, current_snapshot())
    return payload


# --- Repo context ---

@bp.get('/repo-context')
def get_repo_context():
    """The latest repo snapshot: what the app currently is, for the agent and
    for the user to sanity-check."""
    from backend.research.repo_job import current_snapshot
    snapshot = current_snapshot()
    if not snapshot:
        return jsonify(None)
    # `facts` is the raw extraction — tens of KB, and the client only renders
    # the digest. Keep it server-side.
    snapshot.pop('facts', None)
    snapshot['warnings'] = json.loads(snapshot['warnings']) if snapshot.get('warnings') else []
    return jsonify(snapshot)


@bp.post('/repo-context/refresh')
def refresh_repo_context():
    from backend.research.repo_job import run_repo_snapshot
    result = run_repo_snapshot(force=True)
    if result is None:
        return jsonify({'error': 'Not a Lunaschal checkout'}), 400
    return jsonify(result), 201


# --- Discussion (tool-using, streamed) ---

@bp.get('/<idea_id>/conversations')
def list_idea_conversations(idea_id):
    rows = get_db().execute(
        'SELECT * FROM conversations WHERE idea_id=? ORDER BY updated_at DESC',
        (idea_id,),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@bp.post('/<idea_id>/conversations')
def create_idea_conversation(idea_id):
    db = get_db()
    if not db.execute('SELECT 1 FROM ideas WHERE id=?', (idea_id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404
    now = int(time.time())
    conversation_id = str(ULID())
    db.execute(
        'INSERT INTO conversations(id, title, idea_id, created_at, updated_at)'
        ' VALUES (?,?,?,?,?)',
        (conversation_id, (request.json or {}).get('title'), idea_id, now, now),
    )
    db.commit()
    return jsonify({'id': conversation_id}), 201


@bp.post('/<idea_id>/discuss')
def discuss(idea_id):
    """Tool-using discussion, streamed as SSE.

    Gathering and answering are separate turns: the tool loop is blocking
    (llama-server reconstructs tool_calls from a grammar, and reassembling
    partial tool-call deltas is how an argument goes missing), and only the
    final answer streams. Tool events go out as they happen so the 30-90s of
    gathering is legible instead of a spinner.
    """
    from backend.ai import priority
    from backend.ai.llm import chat_stream_deltas
    from backend.research import agent, discuss as ctx

    if not is_ai_configured():
        return jsonify({'error': 'AI provider not configured'}), 400

    body = request.json or {}
    conversation_id = (body.get('conversationId') or '').strip()
    question = (body.get('message') or '').strip()
    if not conversation_id or not question:
        return jsonify({'error': 'conversationId and message required'}), 400

    db = get_db()
    if not db.execute('SELECT 1 FROM ideas WHERE id=?', (idea_id,)).fetchone():
        return jsonify({'error': 'Not found'}), 404

    # Persist the user's turn before streaming: a disconnect mid-answer should
    # not lose the question.
    now = int(time.time())
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, created_at)'
        ' VALUES (?,?,?,?,?)',
        (str(ULID()), conversation_id, 'user', question, now),
    )
    db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, conversation_id))
    db.commit()

    context = ctx.build_context(idea_id)
    history = ctx.history_messages(conversation_id)[:-1]  # drop the turn just saved
    gather_request = ctx.build_gather_request(context, history, question)

    # Acquired in the view, released in the generator's finally — see the same
    # shape in backend/routes/chat.py.
    token = priority.begin('ideas.discuss')

    def generate():
        answer = ''
        steps: list[dict] = []
        sources: list[dict] = []
        try:
            result = {}
            # The generator form, so a tool event reaches the browser the
            # moment that call finishes rather than after all gathering ends.
            for kind, payload in agent.gather_events(ctx.SYSTEM_PROMPT, gather_request):
                if kind == 'step':
                    yield f'data: {json.dumps(payload)}\n\n'
                else:
                    result = payload
            steps = result.get('steps', [])
            sources = result.get('sources', [])

            messages = result.get('messages', []) + [
                {'role': 'user', 'content': ctx.ANSWER_INSTRUCTION}
            ]
            for chunk in chat_stream_deltas(messages):
                answer += chunk
                yield f'data: {json.dumps({"content": chunk})}\n\n'

            message_id = str(ULID())
            finished = int(time.time())
            get_db().execute(
                'INSERT INTO messages(id, conversation_id, role, content, metadata, created_at)'
                ' VALUES (?,?,?,?,?,?)',
                (message_id, conversation_id, 'assistant', answer,
                 json.dumps({'agent': 'ideas', 'steps': steps, 'sources': sources}),
                 finished),
            )
            get_db().execute(
                'UPDATE conversations SET updated_at=? WHERE id=?',
                (finished, conversation_id),
            )
            get_db().commit()
            yield f'data: {json.dumps({"done": True, "messageId": message_id, "sources": sources})}\n\n'
            yield 'data: [DONE]\n\n'
        except Exception as e:
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
        finally:
            priority.end(token)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'},
    )


# --- Plan ---

@bp.get('/<idea_id>/plans')
def list_idea_plans(idea_id):
    from backend.research.plan import list_plans
    return jsonify(list_plans(idea_id))


@bp.get('/plans/<plan_id>')
def get_plan(plan_id):
    row = get_db().execute('SELECT * FROM idea_plans WHERE id=?', (plan_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(row_to_dict(row))


@bp.post('/<idea_id>/plan')
def create_plan(idea_id):
    """Generate a spec for a coding agent. Blocking — it is one long call and
    the user pressed the button."""
    from backend.ai import priority
    from backend.research import assess, discuss as ctx, plan as plan_mod
    from backend.research.repo_job import current_snapshot

    if not is_ai_configured():
        return jsonify({'error': 'AI provider not configured'}), 400

    db = get_db()
    row = db.execute('SELECT * FROM ideas WHERE id=?', (idea_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    idea = row_to_dict(row)

    assessment = assess.latest_assessment(idea_id)
    evidence = json.loads(assessment['evidence']) if assessment else []
    answered = assess.answered_questions(idea_id)
    open_rows = db.execute(
        "SELECT question FROM idea_questions WHERE idea_id=? AND status='open'",
        (idea_id,),
    ).fetchall()
    open_questions = [{'question': r['question']} for r in open_rows]

    prompt = ctx.build_context(idea_id) + (
        '\n\n# Your task\n\nWrite the implementation spec for this idea.'
    )
    with priority.interactive('ideas.plan'):
        spec = plan_mod.generate_spec(prompt)
    if spec is None:
        return jsonify({'error': 'The model returned no usable plan'}), 502

    snapshot = current_snapshot()
    content = plan_mod.render_plan_markdown(
        idea.get('title') or 'Untitled idea',
        spec,
        evidence=evidence,
        answered=answered,
        open_questions=open_questions,
    )
    saved = plan_mod.save_plan(idea_id, content, spec, (snapshot or {}).get('id'))
    db.execute(
        "UPDATE ideas SET status=?, updated_at=? WHERE id=? AND status IN ('new','researching','ready')",
        ('planned', int(time.time()), idea_id),
    )
    db.commit()
    return jsonify(saved), 201


@bp.get('/paper-pages')
def list_paper_pages():
    """Flat feed of every Paper page that has a snapshot, for the sketch picker.

    Flat rather than grouped by paper because the picker is a single scrollable
    grid of pages — the paper title is just a caption on each tile.
    """
    rows = get_db().execute(
        'SELECT p.id, p.paper_id, p.position, p.image_path, p.updated_at,'
        ' d.title AS paper_title'
        ' FROM paper_pages p JOIN papers d ON d.id = p.paper_id'
        ' WHERE p.image_path IS NOT NULL'
        ' ORDER BY d.updated_at DESC, p.position ASC'
    ).fetchall()
    return jsonify([
        {
            'pageId': r['id'],
            'paperId': r['paper_id'],
            'paperTitle': r['paper_title'],
            'position': r['position'],
            'imageUrl': page_image_url(r),
        }
        for r in rows
    ])
