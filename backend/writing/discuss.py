"""The Writing discussion's prompt and scope, decided on the server.

The discussion used to build its system prompt in the browser and send it with
every turn, plus a `writingProjectId` saying which project the tools could
read. That had two costs. The request carried a `systemPrompt`, so it took the
inline path in `backend/routes/chat.py`: no background run, the reply saved by
the browser after the stream ended, and a dropped connection lost it. And a
second client (the iOS app) would have had to reimplement the prompt to take
part.

Now the conversation row is enough. `conversations.writing_project_id` says
which project this is, so the route asks `run_config` and — when it answers —
uses this prompt, this toolset and this scope, ignoring whatever the client
sent. That is also what makes the scope a server fact rather than a claim
from the browser.

What the browser's prompt carried that this one must too: the project's title
and description, and the notes the author marked as mattering on every turn.
Those were a checkbox set held in React state; they are `pinned` on the note
now, which is where the claim belongs — it is about the note, not about one
device's view of it.
"""
from backend.db.connection import get_db
from backend.recall import clip_doc
from backend.writing import canon_tools

# Pinned notes ride in every turn's prompt, so the whole block has a budget.
# Past it the rest are named and left to `writing_read`.
MAX_PINNED_CHARS = 12_000

BRIEF = (
    'This is a brainstorming discussion. Help the author generate and refine '
    'ideas — plot, characters, worldbuilding, themes, and structure. Offer '
    'concrete suggestions with alternatives, point out weaknesses, '
    'contradictions, and clichés honestly instead of praising everything, and '
    'ask sharp questions when an idea is underdeveloped. Build on the '
    "author's ideas rather than replacing them; when the author makes a "
    'decision, treat it as canon for the rest of the discussion. Keep replies '
    'conversational and focused.'
)

NOTEBOOK = (
    "The project's notes are the author's working notebook for the book, and "
    'you help keep it. When something is settled in this discussion — a '
    'character detail, a rule of the world, a change to the outline — you can '
    'propose recording it, as a new note or as an addition to an existing one. '
    'The author confirms each proposal on a card, so propose what was decided, '
    'not every idea that was floated.'
)

CANON = (
    'This story is set in an existing work, and its source material is '
    'searchable with canon_search and canon_read. Canon is what the original '
    "says; the author's notes are where this story diverges from it — so "
    'where the two disagree, the notes win. Look canon up rather than relying '
    'on memory when a detail matters.'
)


def project_for_conversation(conversation_id: str | None) -> str | None:
    if not conversation_id:
        return None
    row = get_db().execute(
        'SELECT c.writing_project_id AS pid FROM conversations c'
        ' JOIN writing_projects p ON p.id = c.writing_project_id WHERE c.id = ?',
        (conversation_id,),
    ).fetchone()
    return row['pid'] if row else None


def _has_canon(project_id: str) -> bool:
    return bool(canon_tools.ready_sources(project_id))


def system_prompt(project_id: str) -> str:
    db = get_db()
    project = db.execute(
        'SELECT title, description FROM writing_projects WHERE id=?', (project_id,)
    ).fetchone()
    title = project['title'] if project else 'Untitled'
    prompt = (f'You are a creative writing partner helping the author develop '
              f'the story "{title}".')
    if project and (project['description'] or '').strip():
        prompt += f'\n\nStory description: {project["description"].strip()}'

    notes = db.execute(
        'SELECT title, doc_type, content, pinned FROM writing_context_docs'
        ' WHERE project_id=? ORDER BY created_at ASC',
        (project_id,),
    ).fetchall()
    pinned = [n for n in notes if n['pinned']]
    if pinned:
        blocks, used, left_out = [], 0, []
        for n in pinned:
            body, _ = clip_doc(n['content'] or '')
            block = f'--- {n["doc_type"]}: {n["title"]} ---\n{body}'
            if used + len(block) > MAX_PINNED_CHARS and blocks:
                left_out.append(n['title'])
                continue
            blocks.append(block)
            used += len(block)
        prompt += '\n\nNotes the author pinned for every turn:\n\n' + '\n\n'.join(blocks)
        if left_out:
            prompt += ('\n\n(Also pinned, but not shown for length — read them '
                       'with writing_read: ' + ', '.join(left_out) + '.)')

    others = [n for n in notes if not n['pinned']]
    if others:
        prompt += '\n\nOther notes in this project (read them with writing_read):\n' + \
            '\n'.join(f'- [{n["doc_type"]}] {n["title"]}' for n in others)

    prompt += f'\n\n{BRIEF}\n\n{NOTEBOOK}'
    if _has_canon(project_id):
        prompt += f'\n\n{CANON}'
    return prompt


def run_config(conversation_id: str | None) -> tuple[str, str, str] | None:
    """(toolset, system_prompt, project_id) for a Writing discussion, else None.

    The one helper both callers ask — the streaming route and the voice-clip
    autoreply — so a dictated turn in a Writing discussion is answered as
    Writing rather than as a Chat-tab turn with the user's life wiki on it.
    """
    project_id = project_for_conversation(conversation_id)
    if not project_id:
        return None
    return 'writing', system_prompt(project_id), project_id
