"""Who produced which part of a text field, so it can be told apart and redone.

Three authors, because they answer "what may be regenerated?" differently:

- `u`, **typed** by the user. Irreplaceable: nothing can produce it again, so a
  regeneration must never touch it.
- `s`, **speech-to-text**. A machine's reading of the user's audio. A better
  STT model can redo it, and a span may name the recording it came from
  (`["s", 120, "<attachment id>"]`) so a re-transcription replaces exactly
  that span and leaves the typing around it alone.
- `a`, **AI-written**: polish, titles, summaries, structured notes, anything an
  LLM or the app itself composed. Redone from its source.

Every client colours by this — blue typed, teal speech-to-text, the normal text
colour for AI — so it is resolved once, here, in SQL, rather than re-derived on
each client:

- A field's spans are a run-length list, `[["s", 40, "<id>"], ["u", 3]]`,
  measured in **Unicode code points** — Python's `len`, SQLite's `length()`,
  JavaScript's `Array.from(s).length`, Swift's `unicodeScalars.count`. Those
  four agree; UTF-16 lengths (`s.length` in JS, `NSString`) do not, and an
  emoji or a combining mark would shift every span after it.
- A stored row is trusted only while the text it was computed for still is
  the field, character for character. A write path that never learned about
  authorship therefore cannot produce wrong spans, only the field's default
  (`DEFAULTS`). `backfill` derives speech-to-text spans for rows written before
  this existed, by finding each recording's transcript in the text.
- A body that still equals its raw text (`MIRRORS`: an unpolished journal entry
  or idea) takes the raw text's spans, recordings included.
- A model write marks the whole field `a` (`mark`). Polish counts as a model
  write; the user's words stay in `raw_content` with their own spans.
- Any other change keeps the authors of whatever it left untouched and gives
  what it inserted to whoever inserted it (`record_change`): a user edit, a
  transcript appended to a note, an accepted Writing proposal. The diff is over
  word and whitespace tokens, so fixing a typo recolours the word rather than
  one stray letter.

`backend/mobile_sync/registry.py` folds the same `sql_object` into the phone's
replica payload, and `backend/mobile_sync/schema.py` re-sends a record when its
spans change, so the phone gets this without computing any of it.
"""
from __future__ import annotations

import difflib
import json
import re
import time

USER = 'u'
SPEECH = 's'
AI = 'a'
AUTHORS = (USER, SPEECH, AI)

_POLISHED = ("CASE WHEN COALESCE({p}.raw_content, '') <> ''"
             " AND {p}.raw_content IS NOT {p}.content THEN 'a' ELSE 'u' END")
_STRUCTURED = ("CASE WHEN {p}.notes IS NOT NULL AND {p}.notes = {p}.generated_notes"
               " THEN 'a' ELSE 'u' END")

# (table, field) -> SQL expression, over the row aliased `{p}`, giving the
# author of the whole field when no valid span row exists. Kept in SQL, not
# Python, so the REST route and the replica trigger evaluate one definition —
# and so a rule may read a column the phone never receives (`generated_notes`).
DEFAULTS: dict[tuple[str, str], str] = {
    ('journal_entries', 'content'): _POLISHED,
    # Filled in by the metadata pass unless the user wrote one; a user rename
    # is recorded, so only titles nobody touched fall back to this.
    ('journal_entries', 'title'): "'a'",
    ('journal_entries', 'raw_content'): "'u'",
    ('food_entries', 'notes'): _STRUCTURED,
    ('food_entries', 'raw_content'): "'u'",
    ('spending_logs', 'notes'): _STRUCTURED,
    ('spending_logs', 'raw_content'): "'u'",
    ('ideas', 'content'): _POLISHED,
    ('ideas', 'title'): "'a'",
    ('ideas', 'raw_content'): "'u'",
    ('messages', 'content'): "CASE WHEN {p}.role = 'user' THEN 'u' ELSE 'a' END",
    ('todos', 'title'): "'u'",
    ('todos', 'notes'): "'u'",
    # The bar is filled by the assistant (add_todos, the briefing, accepted
    # cards), except rows restating the user's own to-do, habit or goal by
    # title; a retitle is recorded either way.
    ('chat_todos', 'title'):
        "CASE WHEN {p}.source_type IN ('todo','daily','goal') THEN 'u' ELSE 'a' END",
    ('chat_todos', 'notes'): "'a'",
    ('writing_chapters', 'title'): "'u'",
    ('writing_chapters', 'content'): "'u'",
    ('writing_context_docs', 'title'): "'u'",
    ('writing_context_docs', 'content'): "'u'",
    ('calendar_events', 'title'): "'u'",
    ('calendar_events', 'description'): "'u'",
    # Written by the chat assistant's create_note_to_self; edits are recorded.
    ('notes_to_self', 'content'): "'a'",
    # Generated unless made by hand; a revision is a new card carrying spans.
    ('learning_cards', 'question'): "CASE WHEN {p}.source_type = 'manual' THEN 'u' ELSE 'a' END",
    ('learning_cards', 'answer'): "CASE WHEN {p}.source_type = 'manual' THEN 'u' ELSE 'a' END",
}

# A field that, while it still equals another, is that other text: the body of
# an entry nobody has polished yet is its raw text, recordings and all.
MIRRORS: dict[tuple[str, str], str] = {
    ('journal_entries', 'content'): 'raw_content',
    ('ideas', 'content'): 'raw_content',
}

FIELDS: dict[str, tuple[str, ...]] = {}
for _table, _field in DEFAULTS:
    FIELDS[_table] = (*FIELDS.get(_table, ()), _field)


def _stored(table: str, field: str, prefix: str) -> str:
    return (f"(SELECT json(ta.spans) FROM text_authorship ta"
            f" WHERE ta.target='{table}' AND ta.row_id={prefix}.id AND ta.field='{field}'"
            f" AND ta.text=COALESCE({prefix}.\"{field}\", ''))")


def sql_expr(table: str, field: str, prefix: str) -> str:
    """JSON spans for one field of the row aliased `prefix`."""
    default = DEFAULTS[(table, field)].format(p=prefix)
    parts = [_stored(table, field, prefix)]
    source = MIRRORS.get((table, field))
    if source:
        parts.append(f"CASE WHEN {prefix}.\"{field}\" = {prefix}.\"{source}\""
                     f" THEN {sql_expr(table, source, prefix)} END")
    parts.append(f"CASE WHEN COALESCE({prefix}.\"{field}\", '')='' THEN json('[]')"
                 f" ELSE json_array(json_array({default}, length({prefix}.\"{field}\"))) END")
    return f"COALESCE({', '.join(parts)})"


def _camel(field: str) -> str:
    head, *rest = field.split('_')
    return head + ''.join(part.capitalize() for part in rest)


def sql_object(table: str, prefix: str) -> str:
    """`{"field": spans, ...}` for every tracked field of `table`, keyed in
    camelCase like every other key the API and the replica send."""
    pairs = ','.join(f"'{_camel(f)}',json({sql_expr(table, f, prefix)})" for f in FIELDS[table])
    return f'json_object({pairs})'


# --- pure span arithmetic ----------------------------------------------------
#
# Internally a span is (author, length, source-or-None); on the wire the source
# is simply left off when there is none.

def _key(item) -> tuple[str, str | None]:
    return item[0], (item[2] if len(item) > 2 else None)


def coalesce(spans) -> list[list]:
    out: list[list] = []
    for item in spans:
        author, source = _key(item)
        n = item[1]
        if n <= 0:
            continue
        if out and _key(out[-1]) == (author, source):
            out[-1][1] += n
        else:
            out.append([author, n] if source is None else [author, n, source])
    return out


def valid(spans, length: int) -> bool:
    if not isinstance(spans, list):
        return False
    total = 0
    for item in spans:
        if (not isinstance(item, (list, tuple)) or len(item) not in (2, 3)
                or item[0] not in AUTHORS or type(item[1]) is not int or item[1] < 0
                or (len(item) == 3 and not isinstance(item[2], str))):
            return False
        total += item[1]
    return total == length


def expand(spans, length: int, fallback: str) -> list[tuple[str, str | None]]:
    """One (author, source) per code point; spans not covering `length` are ignored."""
    if not valid(spans, length):
        return [(fallback, None)] * length
    out: list[tuple[str, str | None]] = []
    for item in spans:
        out.extend([_key(item)] * item[1])
    return out


def runs(text: str, spans, fallback: str) -> list[list]:
    """`[[author, substring], ...]`, as every client slices it for display."""
    out: list[list] = []
    for (author, _), ch in zip(expand(spans, len(text), fallback), text):
        if out and out[-1][0] == author:
            out[-1][1] += ch
        else:
            out.append([author, ch])
    return out


_TOKEN = re.compile(r'\s+|\w+|[^\w\s]', re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def merge_edit(old_text: str, old_spans, new_text: str, *, fallback: str = USER,
               author: str = USER, source: str | None = None) -> list[list]:
    """Spans for `new_text`, given that `author` turned `old_text` into it.

    Tokens the change left in place keep their previous authors and sources,
    character for character; tokens it inserted or replaced are `author`'s.
    """
    old_text = old_text or ''
    new_text = new_text or ''
    authors = expand(old_spans, len(old_text), fallback)
    a, b = _tokens(old_text), _tokens(new_text)
    a_start = [0]
    for t in a:
        a_start.append(a_start[-1] + len(t))
    b_start = [0]
    for t in b:
        b_start.append(b_start[-1] + len(t))
    out: list[list] = []
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == 'equal':
            out.extend([who, 1, src] if src else [who, 1]
                       for who, src in authors[a_start[i1]:a_start[i2]])
        elif op in ('replace', 'insert'):
            n = b_start[j2] - b_start[j1]
            out.append([author, n, source] if source else [author, n])
    return coalesce(out)


def find_spans(text: str, pieces: list[tuple[str, str]], *, rest: str = USER) -> list[list]:
    """Spans for `text` with each (transcript, source) found in it marked `s`.

    For rows written before spans existed: a recording's transcript was pasted
    into the entry verbatim, so where it still appears verbatim, that is where
    it went. Searched left to right, each after the last, as they were appended.
    """
    marks = [(rest, None)] * len(text)
    at = 0
    for piece, source in pieces:
        piece = (piece or '').strip()
        if not piece:
            continue
        found = text.find(piece, at)
        if found < 0:
            found = text.find(piece)
        if found < 0:
            continue
        for i in range(found, found + len(piece)):
            marks[i] = (SPEECH, source)
        at = found + len(piece)
    return coalesce([[who, 1, src] if src else [who, 1] for who, src in marks])


# --- database ----------------------------------------------------------------

def current(db, table: str, row_id: str, field: str) -> tuple[str, list[list]] | None:
    """The field's text and resolved spans, or None if the row doesn't exist."""
    row = db.execute(
        f'SELECT "{field}" AS text, {sql_expr(table, field, "p")} AS spans'
        f' FROM "{table}" p WHERE p.id=?', (row_id,),
    ).fetchone()
    if row is None:
        return None
    return row['text'] or '', json.loads(row['spans'])


def store(db, table: str, row_id: str, field: str, text: str | None, spans) -> None:
    """Record `spans` as describing `text`, the field's value once written."""
    text = text or ''
    db.execute(
        'INSERT INTO text_authorship(target, row_id, field, spans, text, updated_at)'
        ' VALUES (?,?,?,?,?,?) ON CONFLICT(target, row_id, field) DO UPDATE SET'
        ' spans=excluded.spans, text=excluded.text, updated_at=excluded.updated_at',
        (table, row_id, field, json.dumps(coalesce(spans), separators=(',', ':')), text,
         int(time.time())),
    )


def mark(db, table: str, row_id: str, field: str, text: str | None, author: str,
         source: str | None = None) -> None:
    """One author wrote the whole field. The caller owns the transaction."""
    text = text or ''
    store(db, table, row_id, field, text,
          [[author, len(text), source] if source else [author, len(text)]])


def mark_ai(db, table: str, row_id: str, field: str, text: str | None) -> None:
    mark(db, table, row_id, field, text, AI)


def clear(db, table: str, row_id: str, field: str) -> None:
    """Forget stored spans, so the field's default (or mirror) decides again."""
    db.execute('DELETE FROM text_authorship WHERE target=? AND row_id=? AND field=?',
               (table, row_id, field))


def record_change(db, table: str, row_id: str, field: str, new_text: str | None, *,
                  author: str = USER, source: str | None = None) -> list[list] | None:
    """`author` is about to write `new_text` over the field.

    Call it *before* the UPDATE, inside the same transaction: it reads the
    field's current text and spans to diff against. Returns the new spans.
    """
    now = current(db, table, row_id, field)
    if now is None:
        return None
    old_text, old_spans = now
    new_text = new_text or ''
    if new_text == old_text:
        return old_spans
    spans = merge_edit(old_text, old_spans, new_text, author=author, source=source)
    store(db, table, row_id, field, new_text, spans)
    return spans


def record_edit(db, table: str, row_id: str, field: str, new_text: str | None) -> None:
    """The user is about to save `new_text` over the field (see `record_change`)."""
    record_change(db, table, row_id, field, new_text)


def carry(db, table: str, from_id: str, to_id: str, field: str, new_text: str | None,
          *, from_table: str | None = None, from_field: str | None = None,
          author: str = USER) -> None:
    """A new row's field starts from another row's: a split series, a promoted
    to-do. What it kept keeps its authors; anything changed on the way is the
    user's."""
    source = current(db, from_table or table, from_id, from_field or field)
    if source is None or not (new_text or ''):
        return
    old_text, old_spans = source
    store(db, table, to_id, field, new_text,
          merge_edit(old_text, old_spans, new_text or '', author=author))


def snapshot(db, table: str, row_id: str) -> str:
    """Every tracked field's spans as JSON, for a revision table to keep."""
    row = db.execute(f'SELECT {sql_object(table, "p")} AS a FROM "{table}" p WHERE p.id=?',
                     (row_id,)).fetchone()
    return row['a'] if row else '{}'


def restore(db, table: str, row_id: str, saved: str | None, values: dict) -> None:
    """Put a revision's spans back with its text. A revision taken before spans
    were kept has none, and the restored text is then the user's choice."""
    try:
        spans = json.loads(saved) if saved else {}
    except ValueError:
        spans = {}
    for field, text in values.items():
        if (table, field) not in DEFAULTS:
            continue
        kept = spans.get(_camel(field))
        if valid(kept, len(text or '')):
            store(db, table, row_id, field, text, kept)
        else:
            record_change(db, table, row_id, field, text)


def attach(db, table: str, dicts: list[dict]) -> list[dict]:
    """Add `authorship: {field: spans}` to serialized rows of `table`."""
    ids = [d['id'] for d in dicts if d.get('id')]
    if not ids:
        return dicts
    found: dict[str, dict] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        slots = ','.join('?' * len(chunk))
        for row in db.execute(
            f'SELECT p.id, {sql_object(table, "p")} AS a FROM "{table}" p'
            f' WHERE p.id IN ({slots})', chunk,
        ).fetchall():
            found[row['id']] = json.loads(row['a'])
    for d in dicts:
        if d.get('id') in found:
            d['authorship'] = found[d['id']]
    return dicts


# --- rows written before spans existed ----------------------------------------

def _backfill_raw(db, table: str, media_sql: str, field: str = 'raw_content',
                  where: str = '1') -> int:
    done = 0
    rows = db.execute(
        f'SELECT p.id, p."{field}" AS text FROM "{table}" p'
        f" WHERE COALESCE(p.\"{field}\", '') <> '' AND {where}"
        f" AND NOT EXISTS (SELECT 1 FROM text_authorship ta WHERE ta.target='{table}'"
        f" AND ta.row_id=p.id AND ta.field='{field}')"
    ).fetchall()
    for row in rows:
        pieces = [(m[0], m[1]) for m in db.execute(media_sql, (row['id'],)).fetchall()]
        if not pieces:
            continue
        spans = find_spans(row['text'], pieces)
        if any(s[0] == SPEECH for s in spans):
            store(db, table, row['id'], field, row['text'], spans)
            done += 1
    return done


def backfill(db) -> int:
    """Speech-to-text spans for raw text written before they were recorded.

    Idempotent and cheap to re-run: only rows with no stored raw spans are
    looked at, and only those with a recording whose transcript is found in
    them get a row. A voice draft's entry is one transcript end to end.
    """
    done = 0
    for row in db.execute(
        "SELECT e.id, e.raw_content, (SELECT a.id FROM journal_attachments a"
        "  WHERE a.entry_id=e.id AND a.kind='audio' ORDER BY a.position, a.created_at LIMIT 1) AS clip"
        " FROM journal_voice_drafts d JOIN journal_entries e ON e.id=d.entry_id"
        " WHERE COALESCE(e.raw_content, '') <> '' AND NOT EXISTS (SELECT 1 FROM text_authorship ta"
        "  WHERE ta.target='journal_entries' AND ta.row_id=e.id AND ta.field='raw_content')"
    ).fetchall():
        mark(db, 'journal_entries', row['id'], 'raw_content', row['raw_content'], SPEECH,
             row['clip'])
        done += 1
    done += _backfill_raw(
        db, 'journal_entries',
        "SELECT transcript, id FROM journal_attachments WHERE entry_id=? AND kind IN ('audio','video')"
        " AND COALESCE(source_url, '')='' AND COALESCE(transcript, '') <> ''"
        ' ORDER BY position, created_at')
    done += _backfill_raw(
        db, 'food_entries',
        "SELECT transcript, id FROM food_media WHERE entry_id=? AND COALESCE(transcript, '') <> ''"
        ' ORDER BY position, created_at')
    done += _backfill_raw(
        db, 'spending_logs',
        "SELECT transcript, id FROM spending_media WHERE entry_id=? AND COALESCE(transcript, '') <> ''"
        ' ORDER BY position, created_at')
    done += _backfill_raw(
        db, 'messages',
        "SELECT transcript, id FROM chat_attachments WHERE message_id=? AND kind='audio'"
        " AND COALESCE(transcript, '') <> '' ORDER BY created_at, id",
        field='content', where="p.role='user'")
    return done
