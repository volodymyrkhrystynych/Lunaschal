"""The nightly pass that gives journal entries a whole-entry description.

Runs first in the briefing thread (backend/briefing_scheduler.py), before the
life wiki and the briefing, because both read what it writes. One model call per
entry, through backend/ai/entry_digest.py, and the result lands on
`journal_entries.digest`.

Which entries get one: those with attachments, and those whose body is longer
than the briefing shows (`JOURNAL_MAX_CHARS`). A short text-only entry is its
own best description, and describing it would only paraphrase the user's words
back at the readers who most want them verbatim.

When an entry is redone: when what it was built from changed. The inputs —
title, body, and every attachment's reading — are hashed onto
`digest_inputs_hash`, so a caption that lands late, a transcript added the next
day, or an edit to the body all make tomorrow's pass write it again, and
nothing else does. An entry whose attachments are still being read is left for
the next night rather than described from half its inputs.

The window is three days, the life wiki's, for its reason: a night the machine
was off is caught up on the next one. Newest entries go first, so if the budget
runs out it is the entries the briefing is about to read that have one.
`updated_at` is never touched — the digest is derived, and bumping it would
send every described entry back through mobile sync as an edit.
"""
import hashlib
import json
import logging
import time

from backend.ai.service import InferencePaused, Preempted
from backend.db.connection import get_db

logger = logging.getLogger(__name__)

WINDOW_DAYS = 3

# The briefing window is two hours, shared with the life wiki (45 min) and the
# briefing itself. Thirty minutes is ~6 entries with a full thinking budget and
# far more without; most nights need a handful.
DEFAULT_BUDGET_SECONDS = 30 * 60

# A body longer than this is cut off in the briefing (backend/ai/chat.py's
# JOURNAL_MAX_CHARS), so it is worth a description even with nothing attached.
LONG_BODY_CHARS = 2000

# A preempted call is a chat message taking the lane, not a failure; the entry
# is tried again this many times before the pass moves on.
MAX_PREEMPTS_PER_ENTRY = 3


def entry_inputs(db, entry_id: str) -> dict | None:
    """Everything one entry's description is built from."""
    row = db.execute(
        'SELECT id, title, content, raw_content, created_at, digest_inputs_hash'
        ' FROM journal_entries WHERE id=?',
        (entry_id,),
    ).fetchone()
    if row is None:
        return None
    attachments = [dict(a) for a in db.execute(
        'SELECT id, kind, name, transcript, transcript_status, description,'
        ' description_status, import_status, is_screenshot, captured_at, created_at'
        ' FROM journal_attachments WHERE entry_id=? ORDER BY position',
        (entry_id,),
    )]
    return {
        'id': row['id'],
        'title': row['title'],
        # The polished body reads better and has had misheard names fixed; the
        # raw one is the fallback for an entry polish never reached.
        'body': (row['content'] or row['raw_content'] or '').strip(),
        'created_at': row['created_at'],
        'stored_hash': row['digest_inputs_hash'],
        'attachments': attachments,
    }


def needs_digest(entry: dict) -> bool:
    return bool(entry['attachments']) or len(entry['body']) > LONG_BODY_CHARS


def settled(entry: dict) -> bool:
    """False while any attachment is still being read or downloaded.

    A web page `queued` for the browser extension is *not* unsettled: it waits
    durably for a browser to come by, which can be days, and its text arriving
    later changes the inputs hash and redoes the description anyway.
    """
    for a in entry['attachments']:
        if a['transcript_status'] == 'running' or a['description_status'] == 'running':
            return False
        if a['import_status'] == 'importing':
            return False
    return True


def inputs_hash(entry: dict) -> str:
    """What the description was built from, so a change to any of it shows."""
    material = {
        'title': entry['title'] or '',
        'body': entry['body'],
        'attachments': [
            [a['kind'], a['name'], a['transcript'] or '', a['description'] or '',
             bool(a['is_screenshot'])]
            for a in entry['attachments']
        ],
    }
    blob = json.dumps(material, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def _out_of_time(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def run_entry_digest_pass(now: int | None = None, deadline: float | None = None,
                          days: int = WINDOW_DAYS) -> dict:
    """One pass. Returns a summary dict; never raises for an ordinary failure."""
    from backend.ai import entry_digest
    from backend.ai.provider import is_ai_configured

    now = now if now is not None else int(time.time())
    result = {'written': 0, 'cleared': 0, 'unsettled': 0, 'failed': 0,
              'timedOut': False, 'paused': False}
    if not is_ai_configured():
        return result

    db = get_db()
    ids = [r['id'] for r in db.execute(
        'SELECT id FROM journal_entries WHERE created_at >= ?'
        ' ORDER BY created_at DESC, id DESC',
        (now - days * 86400,),
    )]

    for entry_id in ids:
        if _out_of_time(deadline):
            result['timedOut'] = True
            logger.info('Entry digest pass hit its deadline; %d written',
                        result['written'])
            break
        entry = entry_inputs(db, entry_id)
        if entry is None:
            continue
        if not needs_digest(entry):
            # Attachments deleted, or the body trimmed: a description of what
            # is no longer there would be read as if it still were.
            if entry['stored_hash'] is not None:
                db.execute(
                    'UPDATE journal_entries SET digest=NULL, digest_inputs_hash=NULL,'
                    ' digested_at=NULL WHERE id=?',
                    (entry_id,),
                )
                db.commit()
                result['cleared'] += 1
            continue
        if not settled(entry):
            result['unsettled'] += 1
            continue
        digest_hash = inputs_hash(entry)
        if digest_hash == entry['stored_hash']:
            continue

        text = None
        for _attempt in range(MAX_PREEMPTS_PER_ENTRY):
            try:
                text = entry_digest.describe_entry(entry)
                break
            except Preempted:
                continue
            except InferencePaused:
                # The GPU is switched off; every remaining call would say the
                # same. The entries keep their old hash and are redone tomorrow.
                result['paused'] = True
                return result
            except Exception as e:
                logger.warning('Entry digest failed for %s: %s', entry_id, e)
                break
        if not text:
            result['failed'] += 1
            continue

        db.execute(
            'UPDATE journal_entries SET digest=?, digest_inputs_hash=?, digested_at=?'
            ' WHERE id=?',
            (text, digest_hash, now, entry_id),
        )
        db.commit()
        result['written'] += 1

    return result
