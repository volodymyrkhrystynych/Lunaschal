"""Turn what the user says they need to do in the journal into day-plan cards.

The `journal.extract_todos` job (backend/ai/job_handlers.py) runs once an
entry has settled — polished, titled, and still a journal entry after
autofile — and offers each thing heard in it as a pending `todo` card in
*today's* chat, the same card the briefing's carry-overs use, so accepting
one puts it in the bar (backend/routes/chat.py's `_accept_todo`).

Cards, never straight into the bar: auto-adding is what bloated the list the
first time the briefing created to-dos on its own, and an entry is a far
noisier source than a plan. Nothing is offered twice — not a title already in
today's bar or already waiting on a card, not an item linked to a row already
in the bar, and not something the user turned down or has been skipping
(backend/day_plan.py's suggestion history).
"""
import json
import time

from ulid import ULID

from backend import day_plan
from backend.day_boundary import day_key_for


def _entry(db, entry_id: str):
    return db.execute(
        # Not fic commentary, and not a recorded idea: "I need to build X" in
        # either is about the story or the project, not the user's day.
        'SELECT id, title, content, raw_content, created_at FROM journal_entries WHERE id=?'
        ' AND idea_id IS NULL'
        ' AND id NOT IN (SELECT journal_entry_id FROM journal_entry_fic_refs)',
        (entry_id,),
    ).fetchone()


def _already_offered(db, conv_id: str, entry_id: str) -> bool:
    """A replayed job must not stage the entry's cards a second time."""
    return db.execute(
        "SELECT 1 FROM messages WHERE conversation_id=? AND role='assistant'"
        ' AND json_extract(metadata, \'$.journalTodos\')=?',
        (conv_id, entry_id),
    ).fetchone() is not None


def _existing_titles(db, today: str) -> list[str]:
    from backend.ai.briefing import open_todos, pending_daily_tasks
    titles = [r['title'] for r in db.execute('SELECT title FROM chat_todos WHERE day_key=?', (today,))]
    titles += [t['title'] for t in pending_daily_tasks(db, today)]
    titles += [t['title'] for t in open_todos(db)][:40]
    return titles


def offer_todos_from_entry(entry_id: str, now: int | None = None) -> list[dict]:
    """Run the extraction for one entry and stage its cards. Returns the
    proposals staged (possibly none). Model failures propagate — the job
    stays pending through a pause, and errors are recorded as the job's."""
    from backend.ai.journal_todos import extract_journal_todos
    from backend.briefing_job import find_or_create_day_conversation
    from backend.db.connection import get_db
    from backend.goals.service import context_text
    from backend.routes.tasks import _today_bar_sources

    now = now if now is not None else int(time.time())
    db = get_db()
    entry = _entry(db, entry_id)
    if entry is None:
        return []
    today = day_key_for(now)
    # Only today's entries: a backdated or late-processed one belongs to a day
    # whose plan is already over.
    if day_key_for(entry['created_at']) != today:
        return []
    text = (entry['content'] or entry['raw_content'] or '').strip()
    if not text:
        return []

    existing_conv = day_plan.find_day_conversation(db, today)
    if existing_conv and _already_offered(db, existing_conv, entry_id):
        return []

    existing = _existing_titles(db, today)
    items = extract_journal_todos(text, existing=existing, goals=context_text(today))
    if not items:
        return []

    taken = {day_plan.normalize_title(t) for t in
             (r['title'] for r in db.execute('SELECT title FROM chat_todos WHERE day_key=?', (today,)))}
    linked = _today_bar_sources(db, today)
    held = day_plan.held_back_keys(db, today)
    sources = day_plan.list_sources(db, today)
    conv_id = find_or_create_day_conversation(db, today, now)
    taken |= day_plan.pending_card_titles(db, conv_id)

    proposals = []
    for item in items:
        title = item['title']
        norm = day_plan.normalize_title(title)
        if norm in taken:
            continue
        source = day_plan.resolve_list_source(db, today, title, item.get('linkedTitle'), sources)
        if source and source in linked:
            continue
        if day_plan.is_held_back(held, *(source or (None, None)), title):
            continue
        # A link to their list wins: the card puts that to-do on today's plan.
        source_type, source_id = source if source else ('journal', entry_id)
        if source:
            title = day_plan.source_title(db, *source) or title
            # Two wordings of one to-do in the same entry make one card.
            linked.add(source)
        taken.add(norm)
        taken.add(day_plan.normalize_title(title))
        proposals.append({
            'id': str(ULID()), 'kind': 'todo', 'status': 'pending',
            'fromJournal': entry_id,
            'data': {'title': title, 'notes': None, 'due': None, 'priority': 3,
                     'reason': item['reason'] or None,
                     'sourceType': source_type, 'sourceId': source_id},
        })
    if not proposals:
        return []

    label = f"“{entry['title']}”" if entry['title'] else 'your journal'
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, metadata, created_at)'
        ' VALUES (?,?,?,?,?,?)',
        (str(ULID()), conv_id, 'assistant',
         f'From {label} — want {"this" if len(proposals) == 1 else "these"} on today\'s list?',
         json.dumps({'proposals': proposals, 'journalTodos': entry_id}), now),
    )
    db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, conv_id))
    db.commit()
    return proposals
