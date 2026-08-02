"""Running an assessment and clamping it into something trustworthy.

The clamp is the point of this module. A language model asked "is this already
built?" will sometimes answer "yes" fluently while citing nothing, and a
confident wrong "yes" is worse than no assessment at all — it is the one output
that could make the user drop an idea they should have built. So the verdict is
walked back to what the evidence actually supports, deterministically, after
the call.
"""
import json
import logging
import re
import time

from ulid import ULID

from backend.ai.idea_assessment import assess_idea
from backend.db.connection import get_db, row_to_dict
from backend.research import evidence as ev
from backend.research.repo_job import current_snapshot

logger = logging.getLogger(__name__)

VERDICTS = ('no', 'partial', 'yes')
# A single hit is a coincidence — one shared word between an idea and a table
# name proves nothing. Two independent citations is the bar for "yes".
MIN_EVIDENCE_FOR_YES = 2


def question_key(question: str) -> str:
    """Normalized identity for a question, so a re-run does not resurrect one
    the user already answered."""
    return ' '.join(re.sub(r'[^\w\s]', '', (question or '').casefold()).split())


def clamp(result: dict, evidence: list[dict], has_snapshot: bool) -> dict:
    """Walk a raw assessment back to what its evidence supports."""
    verdict = result.get('verdict')
    if verdict not in VERDICTS:
        verdict = 'no'
    try:
        confidence = float(result.get('confidence') or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    rationale = (result.get('rationale') or '').strip()

    if not has_snapshot:
        # Nothing to judge against. Saying so beats guessing from the model's
        # memory of some other codebase.
        return {
            'verdict': 'no',
            'confidence': 0.0,
            'rationale': 'No repo snapshot yet — run the repo-context scan first.',
            'evidence': [],
            'effort': None,
        }

    if not evidence:
        # Claimed without citing anything we can check.
        verdict = 'no'
        confidence = min(confidence, 0.4)
    elif verdict == 'yes' and len(evidence) < MIN_EVIDENCE_FOR_YES:
        verdict = 'partial'
        confidence = min(confidence, 0.6)

    effort = result.get('effort')
    if effort not in ('s', 'm', 'l'):
        effort = None

    return {
        'verdict': verdict,
        'confidence': round(min(max(confidence, 0.0), 1.0), 2),
        'rationale': rationale,
        'evidence': evidence,
        'effort': effort,
    }


def _sync_questions(db, idea_id: str, questions, now: int) -> None:
    """Upsert open questions by key; never touch an answered one."""
    existing = {
        r['question_key']: r
        for r in db.execute(
            'SELECT id, question_key, status FROM idea_questions WHERE idea_id=?',
            (idea_id,),
        ).fetchall()
    }
    for item in questions or []:
        if not isinstance(item, dict):
            continue
        text = (item.get('question') or '').strip()
        if not text:
            continue
        key = question_key(text)
        if not key:
            continue
        options = item.get('options') or []
        options_json = json.dumps([o for o in options if isinstance(o, str)]) or None
        row = existing.get(key)
        if row is None:
            db.execute(
                'INSERT INTO idea_questions(id, idea_id, question, question_key, why,'
                ' options, status, created_at, updated_at)'
                " VALUES (?,?,?,?,?,?,'open',?,?)",
                (str(ULID()), idea_id, text, key, (item.get('why') or '').strip() or None,
                 options_json, now, now),
            )
        elif row['status'] == 'open':
            # Refresh the wording, but an answered or dismissed question stays
            # settled — that is the whole reason keys exist.
            db.execute(
                'UPDATE idea_questions SET question=?, why=?, options=?, updated_at=?'
                ' WHERE id=?',
                (text, (item.get('why') or '').strip() or None, options_json, now, row['id']),
            )
    db.commit()


def answered_questions(idea_id: str) -> list[dict]:
    rows = get_db().execute(
        "SELECT question, answer FROM idea_questions"
        " WHERE idea_id=? AND status='answered' AND answer IS NOT NULL",
        (idea_id,),
    ).fetchall()
    return [{'question': r['question'], 'answer': r['answer']} for r in rows]


def open_question_count(idea_id: str) -> int:
    return get_db().execute(
        "SELECT COUNT(*) AS n FROM idea_questions WHERE idea_id=? AND status='open'",
        (idea_id,),
    ).fetchone()['n']


def latest_assessment(idea_id: str) -> dict | None:
    row = get_db().execute(
        'SELECT * FROM idea_assessments WHERE idea_id=? ORDER BY assessed_at DESC, id DESC LIMIT 1',
        (idea_id,),
    ).fetchone()
    return row_to_dict(row) if row else None


def run_assessment(idea_id: str, now: int | None = None) -> dict | None:
    """Assess one idea against the current repo snapshot.

    Returns the stored assessment, or None when the idea is gone. Writes and
    commits before and after the model call — never across it, because get_db()
    hands out one process-global connection.
    """
    db = get_db()
    row = db.execute('SELECT * FROM ideas WHERE id=?', (idea_id,)).fetchone()
    if not row:
        return None
    idea = row_to_dict(row)
    now = now or int(time.time())

    snapshot = current_snapshot()
    facts = json.loads(snapshot['facts']) if snapshot and snapshot.get('facts') else {}
    candidates = ev.gather_candidates(idea, facts)
    roadmap = ev.roadmap_matches(idea, facts)
    answered = answered_questions(idea_id)

    # No transaction open across the model call.
    result = assess_idea(
        idea,
        ev.render_candidates(candidates),
        len(candidates),
        (snapshot or {}).get('digest') or '',
        roadmap=roadmap,
        answered=answered,
    ) or {}

    chosen = ev.select_by_index(candidates, result.get('evidenceIndexes'))
    clamped = clamp(result, chosen, has_snapshot=snapshot is not None)

    assessment_id = str(ULID())
    db.execute(
        'INSERT INTO idea_assessments(id, idea_id, snapshot_id, verdict, confidence,'
        ' rationale, evidence, on_roadmap, effort, assessed_at, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (
            assessment_id, idea_id, (snapshot or {}).get('id'),
            clamped['verdict'], clamped['confidence'], clamped['rationale'],
            ev.evidence_json(clamped['evidence']),
            json.dumps(roadmap) if roadmap else None,
            clamped['effort'], now, now,
        ),
    )
    db.execute('UPDATE ideas SET assessment_id=? WHERE id=?', (assessment_id, idea_id))
    db.commit()

    _sync_questions(db, idea_id, result.get('openQuestions'), now)
    return latest_assessment(idea_id)


def is_stale(assessment: dict | None, snapshot: dict | None) -> bool:
    """True when the repo has moved on since the verdict was formed."""
    if not assessment:
        return False
    if not snapshot:
        return False
    return assessment.get('snapshotId') != snapshot.get('id')
