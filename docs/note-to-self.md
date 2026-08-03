# Note-to-self — design plan

Status: planning. This is a special kind of learning item that has an answer but no question, stored and surfaced inside the existing Learning tab.

## Goal

Let the user capture a short fact, reminder, or takeaway from chat and save it to the Learning tab as a "note to self" — a learning card without a question. It should be easy to create from a chat command and live alongside flashcards without entering the spaced-repetition review queue.

## Core concept

A note-to-self is like a flashcard with only the answer side:

- **Question**: absent / null.
- **Answer / note**: the text the user wants to remember.
- **Kind**: `note_to_self` (or a similar discriminator).

It lives in the Learning tab so the user can search, browse, and edit it like a card, but it is not quizzed because there is no question to ask.

## Why put it in Learning?

- The Learning tab already stores AI-generated and user-created knowledge snippets.
- Reusing the same schema, storage, and search avoids building a separate notes system.
- A future "All knowledge" search can query cards and notes together.

## Data model (proposed)

Two options. Option A is simpler and recommended to start.

### Option A: extend `learning_cards`

Add a nullable `question` and a `kind` column to the existing `learning_cards` table:

- `kind`: `flashcard` (default) or `note_to_self`.
- `answer`: the note content.
- `question`: nullable; `NULL` for notes.
- FSRS state can be `NULL` for notes; they are never reviewed.

Add a unique index or filter to ensure a note-to-self has `question IS NULL` and `kind = 'note_to_self'`.

### Option B: separate `learning_notes` table

Only consider if cards and notes diverge a lot (e.g., notes need threads, comments, or other metadata).

- `learning_notes(id, content, tags, created_at, updated_at, user_id)`.
- Link to `learning_cards` only if the user later promotes a note into a card.

Recommendation: start with **Option A**.

## Creation paths

### 1. Chat command (primary)

The chat AI should detect a "note to self" intent from messages like:

- "note to self: remember to buy oat milk"
- "make a note to self that I should call the vet on Tuesday"
- "remind me that the PNR code is ABC123"

The chat classifier (`backend/ai/classifier.py`) returns a new intent:

```json
{
  "intent": "note_to_self",
  "content": "PNR code is ABC123",
  "tags": ["travel"]
}
```

### 2. Modal in chat

When the chat backend classifies the message as `note_to_self`, the frontend opens a small modal above or beside the chat:

- Shows the extracted note text (editable).
- Lets the user add tags.
- Offers two buttons: **Save to Learning** and **Cancel**.

After saving, the assistant can confirm: "Saved to your Learning notes."

### 3. Manual creation in Learning tab

A "New note" button in the Learning view opens the same modal, allowing direct creation.

## Chat flow

```
User: "note to self: the project deadline is next Friday"
      │
      ▼
Classifier detects intent: note_to_self
      │
      ▼
Chat UI pauses the normal reply and shows a modal:
      "Save note: 'the project deadline is next Friday'"
      [Tags] [Save] [Cancel]
      │
      ▼
POST /api/learning/notes
      │
      ▼
Saved as a `note_to_self` learning item
```

## API endpoints (proposed)

- `POST /api/learning/notes` — create a note-to-self.
- `GET /api/learning/notes` — list notes (paginated, optionally filtered by tag).
- `PUT /api/learning/notes/<id>` — edit.
- `DELETE /api/learning/notes/<id>` — delete.

`POST /api/learning/cards` can also accept `kind` and a nullable `question` so the same route works for both cards and notes.

## Learning tab UI

- Add a segmented control or tab: **Due for review** / **All cards** / **Notes to self**.
- Notes list: answer text, tags, creation date, edit/delete actions.
- Search: include notes in the existing card search.
- Optional "Promote to flashcard" button on a note, which turns it into a real review card by adding a question.

## AI prompt for the chat classifier

Add a new intent and a constrained output to `backend/ai/classifier.py`:

```
intent: note_to_self
content: the exact text of the note
tags: optional list of relevant tags
```

Example prompt addition:

> If the user says something like "note to self..." or "remind me that...", classify as `note_to_self` and extract the note content.

## Open questions

- Should notes support attachments (audio, images) or stay text-only at first?
- Should notes be included in the daily review count, or kept separate?
- Should the chat modal auto-save if the user sends a bare "note to self" without confirmation, or always ask?
- Should notes be searchable by embedding the same way cards are, or only by FTS?

## Next steps

1. Decide between Option A and Option B for storage.
2. Update the chat classifier to detect `note_to_self` intent.
3. Add `POST /api/learning/notes` and wire the Learning tab UI.
4. Add the chat modal for confirming and saving the note.
