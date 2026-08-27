"""Context and prompts for discussing an idea with the agent.

Unlike Writing discussions — where the frontend assembles the system prompt from
checked notes — this one is built server-side. The wiki, the repo snapshot and
the assessment are all server data; round-tripping them through the browser just
to send them back would be wasteful and would let a stale tab feed the model an
out-of-date picture of the repo.

**The system prompt used to forbid the thing this tab is for.** It said, in so
many words, "do not use them to look up things about Lunaschal itself; the
inventory below is authoritative" — because the agent had no way to look
anything up, and a model guessing about the codebase from a route list was worse
than one that stayed inside the list. Now it can read the code, so the
instruction is inverted: the inventory is an index, the source is the truth, and
an answer about the code is expected to cite `file:line`.
"""
import json

from backend.db.connection import get_db, row_to_dict
from backend.repos import registry
from backend.research import assess, wiki
from backend.research.idea_text import display_title
from backend.research.repo_job import current_snapshot

MAX_DIGEST_CHARS = 8000
MAX_IDEA_CHARS = 4000
MAX_HISTORY_MESSAGES = 12

# A code pass reads far more than a web pass fetches — orienting in an unfamiliar
# module is a list_dir, a search and three reads before anything is understood.
# The web-tuned 12 ran out before the agent had opened anything.
CODE_MAX_TURNS = 24

_BASE_SYSTEM = """You are helping an experienced developer think through an \
idea for a codebase they own — a feature, a refactor, or a suspected bug.

What you produce is read by them and then handed to a coding agent that will \
implement it without talking to you. So be concrete and opinionated:
- Recommend, don't enumerate. If there is a clear best option, say which and why.
- Name real trade-offs, including cost on a machine running a 35B MoE model \
locally on one 8 GB GPU.
- If you did not find something, say so. Never present a guess as a finding."""

_CODE_RULES = """
You can read this repository: search it, open files, list directories{map_note}. \
Use them. The rules that make your answer worth acting on:
- **Check before you assert.** Any claim about how this code behaves must come \
from a file you opened in this conversation. If you did not open it, say you \
did not check.
- **Cite `path/to/file.py:123`.** The person reading this will go and look, and \
a coding agent will start there.
- Say plainly when an idea is already built, or already mostly built, and point \
at the code that does it.
- Prefer extending what is there to adding something parallel to it. Find the \
existing thing first.
- The inventory below is an index, not the truth. It is generated, it can be \
stale, and it does not describe behaviour. The source does."""

_WEB_RULES = """
You can also search and read the web. Use it for how other people solved \
something, or for a fact about a library you are not certain of — not for \
questions about this repository, which you answer by reading it."""

_NO_REPO_RULES = """
You have no checkout of this codebase to read, so you cannot verify anything \
about it. Work from the inventory below, and be explicit that you are reasoning \
from an index rather than from the code. You can still search and read the web."""

ANSWER_INSTRUCTION = (
    'Now answer the owner using what you gathered. Cite each file you read as '
    'path:line, and each web source by name. If you could not confirm something, '
    'say which part and why, rather than filling the gap with a guess.'
)


def system_prompt(has_repo: bool, has_map: bool = False, repo_name: str = '') -> str:
    """The prompt for one discussion, shaped by what tools it actually has.

    Built rather than constant because promising tools that are not in the
    toolbox is the same mistake as offering a tool that always fails: the model
    spends turns reaching for something that is not there.
    """
    parts = [_BASE_SYSTEM]
    if has_repo:
        map_note = (
            ', and look a concept up in the code map to find out what things '
            'are called' if has_map else ''
        )
        parts.append(_CODE_RULES.format(map_note=map_note))
        if repo_name:
            parts.append(f'The repository you are reading is **{repo_name}**.')
        parts.append(_WEB_RULES)
    else:
        parts.append(_NO_REPO_RULES)
    return '\n'.join(parts)


# Kept for the callers that predate per-repo prompts (and for tests that assert
# the shape of a no-repo discussion).
SYSTEM_PROMPT = system_prompt(has_repo=False)


def idea_repo(idea_id: str) -> dict | None:
    """The repo an idea is about: its own, or the default when it has none.

    Falling back to the default is what makes a single-repo setup need no
    configuring — and what lets ideas captured before repositories existed pick
    up code tools without being edited.
    """
    row = get_db().execute('SELECT repo_id FROM ideas WHERE id=?', (idea_id,)).fetchone()
    if row and row['repo_id']:
        repo = registry.get_repo(row['repo_id'])
        if repo and repo.get('cloneState') == 'ready':
            return repo
    return registry.default_repo()


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
        # Deliberately no longer labelled "authoritative": it is generated, it
        # can be stale, and it says nothing about behaviour. It is a map of
        # where to start reading.
        parts.append('# App inventory (an index — verify against the source)\n\n'
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


def build_toolbox(repo: dict | None):
    """(tools, dispatch, code_tools) for one discussion.

    The three toolboxes compose rather than replace each other: code for this
    repo, the web for prior art, the wiki for what has already been written
    down. `tools` and `dispatch` travel together — a tool the model can see but
    the dispatch cannot run comes back as "Unknown tool", which reads to the
    model as a broken tool rather than as one it should not have called.

    `code_tools` is handed back so the caller can ask what was actually read;
    it is per-run state and must not be shared between runs.
    """
    from backend.research import agent, code

    tools = list(web_and_wiki_tools())
    dispatch = dict(agent._DISPATCH)
    code_tools = None

    root = repo_root_for(repo)
    if root is not None:
        code_tools = code.CodeTools(root)
        tools = code.tools_for(root) + tools
        dispatch.update(code.dispatch_for(code_tools, root))
    return tools, dispatch, code_tools


def web_and_wiki_tools() -> list[dict]:
    from backend.research import web, wiki as wiki_mod
    return web.TOOLS + wiki_mod.TOOLS


def repo_root_for(repo: dict | None):
    """The checkout to read, or None when there is nothing usable to read."""
    if not repo or repo.get('cloneState') != 'ready':
        return None
    from backend.repos import storage
    root = storage.repo_dir(repo['slug'])
    return root if root and root.is_dir() else None
