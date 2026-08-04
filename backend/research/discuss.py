"""Context and prompts for discussing an idea with the agent.

Unlike Writing discussions — where the frontend assembles the system prompt from
checked notes — this one is built server-side. The wiki, the repo snapshot and
the assessment are all server data; round-tripping them through the browser
just to send them back would be wasteful and would let a stale tab feed the
model an out-of-date picture of the repo.
"""
import json

from backend.db.connection import get_db, row_to_dict
from backend.research import assess, wiki
from backend.research.idea_text import display_title
from backend.research.repo_job import current_snapshot

MAX_DIGEST_CHARS = 8000
MAX_IDEA_CHARS = 4000
MAX_HISTORY_MESSAGES = 12

SYSTEM_PROMPT = """You are helping the owner of Lunaschal — a single-user, \
local-first life-management app — think through a feature idea for it.

You have tools: search and read the web, and read a wiki of research notes you \
maintain yourself. Use them when the answer depends on how other people have \
solved something, or on facts you are not certain of. Do not use them to look \
up things about Lunaschal itself; the inventory below is authoritative.

Be concrete and opinionated. The owner is an experienced developer building for \
themselves, so:
- Recommend, don't enumerate. If there is a clear best option, say which and why.
- Say when an idea is already built, or already mostly built, and point at what exists.
- Name real trade-offs, including cost on a machine running a 26B model locally \
on one 8 GB GPU.
- If you did not find something, say so. Never present a guess as a finding."""

ANSWER_INSTRUCTION = (
    'Now answer the owner using what you gathered. Cite any web source you '
    'actually read by name. If your search came up short, say so plainly '
    'rather than filling the gap with a guess.'
)


def build_context(idea_id: str) -> str:
    """Everything the agent should know before the conversation starts."""
    db = get_db()
    row = db.execute('SELECT * FROM ideas WHERE id=?', (idea_id,)).fetchone()
    if not row:
        return ''
    idea = row_to_dict(row)

    parts = [
        '# The idea\n\n'
        + display_title(idea)
        + '\n\n'
        + (idea.get('content') or idea.get('rawContent') or '')[:MAX_IDEA_CHARS]
    ]

    assessment = assess.latest_assessment(idea_id)
    if assessment:
        lines = [
            f"Already implemented: **{assessment['verdict']}** "
            f"(confidence {assessment['confidence']})",
            assessment['rationale'],
        ]
        try:
            for item in json.loads(assessment['evidence'] or '[]'):
                lines.append(f"- {item['kind']}: {item['ref']} ({item.get('file')})")
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
        parts.append('# Prior assessment\n\n' + '\n'.join(lines))

    questions = db.execute(
        'SELECT question, why, answer, status FROM idea_questions WHERE idea_id=?'
        " ORDER BY status, created_at",
        (idea_id,),
    ).fetchall()
    open_q = [q for q in questions if q['status'] == 'open']
    answered = [q for q in questions if q['status'] == 'answered' and q['answer']]
    if answered:
        parts.append(
            '# Decisions already made\n\n'
            + '\n'.join(f"- {q['question']} → {q['answer']}" for q in answered)
        )
    if open_q:
        parts.append(
            '# Still undecided\n\n'
            + '\n'.join(f"- {q['question']}" + (f" ({q['why']})" if q['why'] else '')
                        for q in open_q)
        )

    # Sketch captions, not the images: vision is off in this project, so the
    # caption is the only thing the model can actually read.
    sketches = db.execute(
        'SELECT caption FROM idea_sketches WHERE idea_id=? AND caption != ""'
        ' ORDER BY position',
        (idea_id,),
    ).fetchall()
    if sketches:
        parts.append(
            '# Sketches the owner drew (described by them, you cannot see the images)\n\n'
            + '\n'.join(f"- {s['caption']}" for s in sketches)
        )

    linked = wiki.articles_for_idea(idea_id)
    if linked:
        parts.append(
            '# Research notes already linked to this idea\n\n'
            + '\n'.join(f"- {a['slug']}: {a['title']} — {a['summary']}" for a in linked)
            + '\n\nRead any of these in full with wiki_read.'
        )

    snapshot = current_snapshot()
    if snapshot and snapshot.get('digest'):
        parts.append('# App inventory (authoritative)\n\n'
                     + snapshot['digest'][:MAX_DIGEST_CHARS])

    return '\n\n'.join(parts)


def history_messages(conversation_id: str) -> list[dict]:
    """The recent turns, tail-truncated to fit alongside the tool transcript."""
    rows = get_db().execute(
        "SELECT role, content FROM messages WHERE conversation_id=?"
        " AND role IN ('user','assistant') ORDER BY created_at",
        (conversation_id,),
    ).fetchall()
    recent = rows[-MAX_HISTORY_MESSAGES:]
    return [{'role': r['role'], 'content': r['content']} for r in recent]


def build_gather_request(context: str, history: list[dict], question: str) -> str:
    """One user-role blob for the gathering loop.

    The tool loop gets the conversation flattened into a single message rather
    than a real multi-turn history: it only needs to know what to look up, and
    a flat brief keeps the turn short, which is what makes the loop
    interruptible (see backend/ai/priority.py).
    """
    parts = [context] if context else []
    if history:
        transcript = '\n\n'.join(
            f"{'Owner' if m['role'] == 'user' else 'You'}: {m['content']}"
            for m in history
        )
        parts.append(f'# Conversation so far\n\n{transcript}')
    parts.append(f'# What the owner just asked\n\n{question}')
    parts.append(
        'Gather anything you need with the tools before answering. If you '
        'already know enough, reply with a short note saying so.'
    )
    return '\n\n'.join(parts)
