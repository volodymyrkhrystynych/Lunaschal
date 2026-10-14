"""Learnings a coding agent wrote down, imported and turned into cards.

The agent side is only a convention (docs/agent-learnings.md): one Markdown
file per day, one `## ` section per lesson. Nothing here talks to an agent.
Files arrive two ways — uploaded or pasted in the Learning tab, or picked up
from an inbox folder on the server — and both go through `import_text`.

Each lesson is one `learning_lessons` row and one `learning.process_lesson`
job. The job asks the model whether an existing card already covers the
lesson (shown only the nearest few by embedding) and makes scenario cards in
the approval queue only when none does. A covered lesson keeps the card and
the reason, so a wrong verdict can be overridden with `force`.

Dedup is on the lesson's text, never the file. A day's file grows while the
day goes on, and the inbox is rescanned whenever the Learning tab opens, so
re-reading a file must add exactly the sections that are new. The user's
files are only ever read: nothing is moved, renamed or deleted.
"""
import hashlib
import re
import time
from pathlib import Path

from ulid import ULID

from backend.ai import jobs, service
from backend.learning.dedup import cosine, embed_answer
from backend.tags import tags_json

JOB_KIND = 'learning.process_lesson'
SOURCE_TYPE = 'agent_lesson'
CARD_TAG = 'lesson'

# How many existing cards the coverage judge is shown, and how similar one
# must be to be shown at all. Low on purpose: the judge is the filter, this
# only keeps unrelated cards out of its prompt.
MAX_CANDIDATES = 5
CANDIDATE_FLOOR = 0.55

# A learnings file is a page of notes. Anything this large is not one, and is
# skipped rather than sent to the model a section at a time.
MAX_FILE_BYTES = 512 * 1024

_HEADING = re.compile(r'^##\s+(.+?)\s*#*\s*$')


def parse_lessons(text: str) -> list[dict]:
    """Split a learnings file into `{title, body}` lessons, one per `## `.

    A `# ` heading (the file's date) and anything before the first `## ` is
    preamble and dropped. A file with no `## ` at all is one lesson whose
    title is its first line — that is what pasting a single note looks like.
    """
    lessons: list[dict] = []
    current: dict | None = None
    preamble: list[str] = []
    for line in (text or '').splitlines():
        m = _HEADING.match(line)
        if m:
            current = {'title': m.group(1).strip(), 'lines': []}
            lessons.append(current)
        elif current is not None:
            current['lines'].append(line)
        else:
            preamble.append(line)

    if not lessons:
        rows = [l for l in preamble if l.strip() and not l.lstrip().startswith('# ')]
        if not rows:
            return []
        return [{'title': rows[0].strip().lstrip('-* ').strip(),
                 'body': '\n'.join(rows[1:]).strip()}]

    out = []
    for lesson in lessons:
        body = '\n'.join(lesson['lines']).strip()
        if lesson['title'] or body:
            out.append({'title': lesson['title'], 'body': body})
    return out


def lesson_hash(title: str, body: str) -> str:
    """Whitespace and case don't make a lesson new; anything else does."""
    norm = ' '.join(f'{title}\n{body}'.lower().split())
    return hashlib.sha256(norm.encode('utf-8')).hexdigest()


def import_text(db, text: str, *, source: str, source_name: str | None,
                folder_id: str | None) -> dict:
    """Store each lesson not seen before and queue it for processing."""
    added, seen = [], 0
    now = int(time.time())
    for lesson in parse_lessons(text):
        lesson_id = str(ULID())
        cur = db.execute(
            'INSERT OR IGNORE INTO learning_lessons'
            ' (id, source, source_name, title, body, content_hash, folder_id, created_at)'
            ' VALUES (?,?,?,?,?,?,?,?)',
            (lesson_id, source, source_name, lesson['title'], lesson['body'],
             lesson_hash(lesson['title'], lesson['body']), folder_id, now),
        )
        if cur.rowcount:
            added.append(lesson_id)
        else:
            seen += 1
    db.commit()
    for lesson_id in added:
        jobs.enqueue(JOB_KIND, lesson_id)
    return {'added': len(added), 'alreadyImported': seen, 'ids': added}


# ------------------------------------------------------------------- the inbox

def settings(db) -> dict:
    row = db.execute(
        'SELECT learnings_inbox_path, learnings_folder_id FROM settings WHERE id=1'
    ).fetchone()
    return {
        'inboxPath': (row['learnings_inbox_path'] or '') if row else '',
        'folderId': (row['learnings_folder_id'] or None) if row else None,
    }


def inbox_state(path: str) -> str:
    """'unset' | 'missing' | 'ok'. A folder that isn't there is reported,
    never created: it may be a mount that isn't up yet."""
    if not path.strip():
        return 'unset'
    return 'ok' if Path(path).expanduser().is_dir() else 'missing'


def scan_inbox(db) -> dict:
    """Import every `*.md` in the inbox folder. Cheap to repeat: files are
    read in full each time and only unseen lessons are inserted."""
    cfg = settings(db)
    state = inbox_state(cfg['inboxPath'])
    result = {'state': state, 'files': 0, 'added': 0, 'alreadyImported': 0, 'skipped': []}
    if state != 'ok':
        return result
    root = Path(cfg['inboxPath']).expanduser()
    for path in sorted(root.glob('*.md')):
        if not path.is_file():
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            result['skipped'].append(path.name)
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        counts = import_text(db, text, source='inbox', source_name=path.name,
                             folder_id=cfg['folderId'])
        result['files'] += 1
        result['added'] += counts['added']
        result['alreadyImported'] += counts['alreadyImported']
    return result


# ------------------------------------------------------------- the background job

def _candidates(db, lesson_id: str, embedding: bytes | None) -> list[dict]:
    """The cards nearest the lesson, live or still in the queue — a lesson
    imported twice in one batch must find the cards its twin just made."""
    if embedding is None:
        return []
    rows = db.execute(
        "SELECT id, question, answer, answer_embedding FROM learning_cards"
        " WHERE state IN ('active','pending') AND kind='qa'"
        " AND answer_embedding IS NOT NULL"
        # IS NOT rather than NOT (=): most cards have a NULL source_type,
        # and NOT (NULL AND …) would quietly exclude them all.
        " AND (source_type IS NOT ? OR source_id IS NOT ?)",
        (SOURCE_TYPE, lesson_id),
    ).fetchall()
    scored = []
    for row in rows:
        score = cosine(embedding, row['answer_embedding'])
        if score is not None and score >= CANDIDATE_FLOOR:
            scored.append((score, row))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [{'id': r['id'], 'question': r['question'], 'answer': r['answer']}
            for _, r in scored[:MAX_CANDIDATES]]


def _set(db, lesson_id: str, **cols) -> None:
    cols['processed_at'] = int(time.time())
    assignments = ', '.join(f'{k}=?' for k in cols)
    db.execute(f'UPDATE learning_lessons SET {assignments} WHERE id=?',
               (*cols.values(), lesson_id))
    db.commit()


def process_lesson(lesson_id: str, *, force: bool = False) -> None:
    """Judge one lesson against the deck, then card it or mark it covered.

    `force` skips the judge — the user has read the verdict and disagrees.
    Re-running a finished lesson without it is a no-op, so a job replayed
    after a restart cannot card a lesson twice.
    """
    from backend.ai.learning_lessons import generate_lesson_cards, judge_coverage
    from backend.db.connection import get_db
    from backend.routes.learning import _insert_cards

    db = get_db()
    row = db.execute('SELECT * FROM learning_lessons WHERE id=?', (lesson_id,)).fetchone()
    if row is None or (row['status'] in ('carded', 'covered') and not force):
        return
    text = f"{row['title']}\n\n{row['body']}".strip()
    try:
        if not force:
            candidates = _candidates(db, lesson_id, embed_answer(text))
            index, reason = judge_coverage(row['title'], row['body'], candidates)
            if index is not None:
                _set(db, lesson_id, status='covered', covered_by=candidates[index]['id'],
                     reason=reason, card_count=0, error=None)
                return
        cards = generate_lesson_cards(row['title'], row['body'])
        if not cards:
            _set(db, lesson_id, status='error', error='No cards could be generated')
            return
        ids = _insert_cards(
            cards, folder_id=row['folder_id'], tags=tags_json([CARD_TAG]),
            source_type=SOURCE_TYPE, source_id=lesson_id, derived_from=None,
            generation_context=text[:8000],
        )
        _set(db, lesson_id, status='carded', card_count=row['card_count'] + len(ids),
             covered_by=None, reason=None, error=None)
    except (service.InferencePaused, service.Preempted):
        # Not a failure: the job goes back to pending and so does the lesson.
        raise
    except Exception as e:
        _set(db, lesson_id, status='error', error=str(e) or 'Failed')
        raise
