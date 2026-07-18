# Roadmap

Future features and larger work items for Lunaschal. Roughly ordered within each section; nothing here is scheduled.

## Backups (mandatory)

Basically mandatory — as email, journals, handwritten pages, and the book library accumulate, `data/lunaschal.db` becomes irreplaceable.

- **Local backup**: scheduled snapshot of the SQLite DB (+ media files) to another directory/disk.
- **Cloud backup**: push to **Dropbox or Google Drive** — but **zipped and encrypted** before upload; the cloud never sees plaintext.
- **Key management**: random 256-bit key generated once when backups are enabled; archives encrypted with AES-256-GCM (or `age`). Key stored in `~/.config/lunaschal/backup.key` (chmod 600) — _never_ in the settings table (it's inside the DB being backed up — lockout after disk loss) and _never_ uploaded with the archive. On generation, show the key once so it can be saved in a password manager / printed; that off-machine copy is what makes restore after total machine loss possible.
- Retention policy (keep N daily / weekly snapshots) and a tested restore path — a backup that's never been restored doesn't count.
- Plain-format export (Markdown/EPUB) as a secondary escape hatch so nothing is locked into the DB.

## Email ingestion

Pull email out of cloud providers and into Lunaschal so it can be stored locally and processed by the AI layer.

- Connect to **Gmail** and **Outlook** (OAuth), fetch messages, and save them locally — get the mail _off_ the cloud, not just read it there.
- Store full messages (headers, body, attachments) in SQLite alongside the existing journal/RAG data.
- AI processing on ingested mail:
  - Summaries (per message and per thread)
  - Auto-tagging (reuse the curated-tags classifier approach in `backend/ai/journal.py`)
  - Routing / "figuring out what goes where" — classify mail into todos, calendar events, journal-worthy items, or archive, similar to the intent classifier in `backend/ai/classifier.py`
- Feed ingested mail into RAG so chat can answer questions about it.

## Calendar sync

- Import **Google Calendar** events and save them into the local `calendar` tables, so all dates live in Lunaschal.
- Decide sync direction: start with one-way import; two-way sync (create in Lunaschal → push to Google) is a possible follow-up.
- Handle recurring events and updates/deletions on re-sync.

## Todos

Flesh out the todo section (`backend/routes/tasks.py`, currently basic CRUD + reorder/complete):

- Due dates, priorities, and recurring todos
- Subtasks / checklists
- Better frontend view (grouping, filtering, overdue highlighting)
- Tighter integration with voice commands and (eventually) email routing above

## Library — saved books & fanfics

A place to save books that have been read. Most are fanfics published as forum threads, so this needs a scraper/compiler:

- Fetch a forum thread and extract the story posts — **each consecutive author post becomes a chapter** (e.g. threadmarked posts on SpaceBattles/Sufficient Velocity style forums).
- Compile the chapters into a single local book (Markdown or EPUB) and store it in the library.
- Library UI: list of saved books with title, author, source URL, read status, and maybe tags/notes.
- Later: AI summaries per book, and reading-history stats.

## Flashcards — Anki parity review

Compare the current flashcard system (SM-2 in `backend/routes/flashcard.py`) against **Anki** to figure out what's missing. Candidates to evaluate:

- Scheduler: Anki's modern FSRS algorithm vs. plain SM-2; configurable learning steps, lapses, leech handling
- Deck options: per-deck settings, daily new/review limits
- Card types: cloze deletions, reversed cards, multiple fields per note
- Media on cards (images, audio)
- Import/export (`.apkg` compatibility would let existing Anki decks come over)

## Flashcards — spoken answers

An alternative to flip-and-self-grade flashcards: answer out loud and let the AI judge.

- On showing a question, record the user's **spoken answer** (reuse the existing STT pipeline, `POST /api/transcribe`).
- An LLM compares the transcribed answer against the card's actual answer and grades it — **bad / good / excellent**.
- The grade maps onto the spaced-repetition rating (SM-2 quality score) instead of manual self-assessment.
- Optionally show the judgment with a short explanation of what was missing or wrong.

## Journal — drawing / handwritten pages

Support pen input in the journal, matching an iPad + stylus "pen on PDF planner" workflow:

- A standard **PDF planner template** that gets copied for each day; the day's handwriting happens on top of it.
- Multiple handwritten pages per day.
- Drawing/ink layer usable from a tablet (Apple Pencil on iPad), saved locally with the journal entry.
- Open questions: render/annotate PDF in-app vs. import annotated PDFs; whether to attempt handwriting OCR so entries become searchable/taggable.

## Journal — views & tagging

- View modes: **calendar view**, **list view**, and **day view**.
- **"On this day last year"** — show past entries from the same date in previous years.
- Better tag coverage: get good tags on _all_ journal entries (backfill with the curated-tags classifier, improve freeform AI tagging quality).

## Mobile / tablet access

**Decision: web-first (responsive UI / PWA), not a native app.** The React frontend and network mode already exist, so this is incremental; a native iPad app would mean a second codebase plus Mac/Xcode tooling. Apple Pencil works in Safari (pointer events with pressure/tilt, low-latency canvas), so browser drawing should be tried first — revisit native only if ink latency disappoints.

- Make the frontend responsive and installable as a PWA over network mode.
- Primary uses: iPad drawing/handwriting (see journal drawing section), quick capture of todos/journal entries from a phone, flashcard review away from the desk.
- **Offline** — scope is capture-and-sync, not full sync: create new journal entries (and todos, ink) while offline, queue them locally (service worker + IndexedDB), and push them to the desktop server once back online. New-record-only upload means no conflict resolution needed. Full two-way sync of the dataset is explicitly out of scope.

## Global search

One search box across everything: journal, emails, books, writing projects, files, todos.

- Extend the existing FTS5 setup (currently `journal_fts`) to the other content types.
- Blend keyword results with RAG semantic search for the "I don't remember the exact words" case.

## AI reviews & check-in redo

- **Weekly / monthly AI review**: summarize the period's journal entries, completed vs. slipped todos, and calendar; surface patterns. Pairs with "on this day last year."
- **Redo morning check-ins** (`stt/morning_checkin.py`) — the current wake-detection flow needs a rework.

## Habit tracking

- Lightweight habit grid in the journal day view: define habits, check them off daily.
- **Streaks** and simple stats over time.
- Fits alongside the planner-style daily pages and calendar/day views.

## Bug fixing

General stability pass — lots of small bugs to track down and fix. (Add specific known bugs here as they're identified.)

-
