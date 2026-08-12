"""Background check: does a homemade-sounding food entry match an existing
recipe?

Runs after a food entry is created (backend/routes/food.py's
structure_food_entry, backend/routes/chat.py's _accept_food) on a background
thread, same as the other food/journal AI passes — never raises, and never
fires twice for the same entry (`food_entries.recipe_match_status` is the
idempotency guard). A strong match doesn't get linked automatically: it drops
a `recipe_link` confirm card into today's chat, the same generic proposal
system propose_food_log and friends already use
(backend/delegate/tools.py, resolve_proposal in backend/routes/chat.py) — the
card just comes from a background job instead of the decision turn.
"""
import json
import time

from ulid import ULID

from backend.ai.food import classify_homemade_match
from backend.briefing_job import find_or_create_day_conversation
from backend.chat_day import day_key_for
from backend.db.connection import get_db, search_recipes_fts

# Candidates handed to the model: an FTS match on the dish name first (most
# relevant, and cheap), falling back to the most recent recipes when the dish
# name doesn't hit anything in the index — a homemade dish is often named
# more casually than the recipe it was cooked from.
MAX_FTS_CANDIDATES = 15
MAX_FALLBACK_CANDIDATES = 30


def _candidates(db, dish: str) -> list[dict]:
    fts = search_recipes_fts(dish, MAX_FTS_CANDIDATES)
    ids = [r['id'] for r in fts]
    if ids:
        placeholders = ','.join('?' * len(ids))
        rows = db.execute(
            f'SELECT id, title, tags FROM recipes WHERE id IN ({placeholders})', ids,
        ).fetchall()
        order = {id_: i for i, id_ in enumerate(ids)}
        rows = sorted(rows, key=lambda r: order.get(r['id'], 0))
    else:
        rows = db.execute(
            'SELECT id, title, tags FROM recipes ORDER BY created_at DESC LIMIT ?',
            (MAX_FALLBACK_CANDIDATES,),
        ).fetchall()

    out = []
    for r in rows:
        tags = []
        if r['tags']:
            try:
                parsed = json.loads(r['tags'])
                tags = parsed if isinstance(parsed, list) else []
            except (ValueError, TypeError):
                tags = []
        out.append({'id': r['id'], 'title': r['title'], 'tags': tags})
    return out


def _mark(db, entry_id: str, status: str) -> None:
    db.execute('UPDATE food_entries SET recipe_match_status=? WHERE id=?', (status, entry_id))
    db.commit()


def _propose_link(db, entry_id: str, dish: str, recipe: dict) -> None:
    now = int(time.time())
    conv_id = find_or_create_day_conversation(db, day_key_for(now), now)
    proposal = {
        'id': str(ULID()),
        'kind': 'recipe_link',
        'data': {
            'entryId': entry_id,
            'recipeId': recipe['id'],
            'dish': dish,
            'recipeTitle': recipe['title'],
        },
        'status': 'pending',
        'resolvedAt': None,
    }
    content = (
        f'That {dish} looks like it might be your "{recipe["title"]}" recipe — '
        'want me to link them?'
    )
    message_id = str(ULID())
    db.execute(
        'INSERT INTO messages(id, conversation_id, role, content, metadata, created_at)'
        ' VALUES (?,?,?,?,?,?)',
        (message_id, conv_id, 'assistant', content,
         json.dumps({'proposals': [proposal]}), now),
    )
    db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now, conv_id))
    _mark(db, entry_id, 'proposed')


def check_homemade_recipe_match(entry_id: str) -> None:
    """Look at one newly-created food entry and, if it reads as homemade and
    strongly matches something already in the recipe collection, offer to
    link them. No-ops (silently) when the entry is already linked, was
    already checked, has no dish, or there's nothing in the collection to
    match against."""
    try:
        db = get_db()
        row = db.execute(
            'SELECT dish, place, notes, recipe_id, recipe_match_status'
            ' FROM food_entries WHERE id=?',
            (entry_id,),
        ).fetchone()
        if not row or row['recipe_id'] or row['recipe_match_status'] or not row['dish']:
            return

        candidates = _candidates(db, row['dish'])
        if not candidates:
            _mark(db, entry_id, 'none')
            return

        result = classify_homemade_match(row['dish'], row['place'], row['notes'], candidates)
        if not result or not result['homemade'] or result['matchIndex'] is None \
                or result['confidence'] == 'low':
            _mark(db, entry_id, 'none')
            return

        recipe = candidates[result['matchIndex'] - 1]
        _propose_link(db, entry_id, row['dish'], recipe)
    except Exception as e:
        # Left unmarked (NULL) on failure rather than 'none', so a future
        # retrigger isn't permanently blocked by a transient AI outage.
        print(f'Homemade recipe match check failed: {e}')
