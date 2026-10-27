# Roadmap

Future features and larger work items for Lunaschal. Roughly ordered within each section; nothing here is scheduled.

**Status audited against `main` on 2026-10-27.** Every item carries a marker: ✅ built, 🟡 partly built (what's missing is said), ⬜ not started. Sections that are entirely built have moved to [Done](#done) at the bottom, with a pointer to where each one lives now.

## Apple apps and offline experience

The active plan for native iPhone/iPad capture, multi-device synchronization,
downloadable libraries, PencilKit drawing, and the Watch recording companion is
tracked in [Apple/offline implementation](apple-offline-implementation.md).
That document separates implemented work from automated/device verification and
tracks the hosted Mac/TestFlight build path and optional on-device AI.

## Backups (mandatory)

Basically mandatory — as email, journals, handwritten pages, and the book library accumulate, `data/lunaschal.db` becomes irreplaceable.

- ✅ **Local backup**: nightly `ops/backup.sh` (rsync snapshots of the DB + media to another disk), destination and retention set in Settings → Backup, watched by the age of the newest snapshot on the drive (`backend/ops/backup_status.py`).
- ✅ Retention policy — `settings.backup_retention_days`.
- 🟡 **Tested restore path** — a manual procedure is written up in [deployment.md](./deployment.md), but nothing exercises it automatically. A backup that's never been restored doesn't count.
- ⬜ **Encryption** — snapshots are plaintext. Plan: random 256-bit key generated once when backups are enabled; archives encrypted with AES-256-GCM (or `age`). Key stored in `~/.config/lunaschal/backup.key` (chmod 600) — _never_ in the settings table (it's inside the DB being backed up — lockout after disk loss) and _never_ uploaded with the archive. On generation, show the key once so it can be saved in a password manager / printed; that off-machine copy is what makes restore after total machine loss possible.
- ⬜ **Cloud backup**: push to **Dropbox or Google Drive** — zipped and encrypted before upload; the cloud never sees plaintext. Depends on encryption above.
- ⬜ Plain-format export (Markdown/EPUB) as a secondary escape hatch so nothing is locked into the DB.

## Email ingestion

Mail is pulled off the cloud and stored locally (`backend/email/`); what's left is doing more with it.

- ✅ Connect to **Gmail**, **Outlook** and **IMAP**, fetch messages, and save them locally (headers, plain + HTML bodies, inline images).
- 🟡 **Attachments** — inline (`cid:`) images are fetched; ordinary file attachments are not stored.
- ✅ **Auto-tagging** — closed-vocabulary category classification (`backend/ai/email.py`), plus job-application status linkage (`backend/jobs/`).
- ⬜ **Summaries** (per message and per thread).
- ⬜ **Routing** — classify mail into todos, calendar events or journal-worthy items. Only the job-application linkage exists today.
- 🟡 **Chat can answer questions about mail** — `emails_fts` exists and backs the Email view's search, but no chat/delegate tool reads it.

## Calendar sync

- ⬜ Import **Google Calendar** events and save them into the local `calendar` tables, so all dates live in Lunaschal.
- ⬜ Decide sync direction: start with one-way import; two-way sync (create in Lunaschal → push to Google) is a possible follow-up.
- ⬜ Handle recurring events and updates/deletions on re-sync.

## Todos

The todo section (`backend/routes/tasks.py`, `src/components/Tasks/`, shown inside Lifestyle):

- ✅ Due dates, priorities, and recurring todos (`due`, `priority`, `repeat_interval`/`repeat_unit`; date math in `backend/todo_recurrence.py`).
- ✅ Overdue highlighting and a priority flag in the list (`src/lib/todos.ts`).
- ⬜ Subtasks / checklists.
- ⬜ Grouping (Overdue / Today / Upcoming / Someday) and filtering in the frontend.
- 🟡 Integration with voice and email — the chat delegate's `add_todos` tool creates todos from a spoken or typed chat message; email routing (above) does not exist.

## Flashcards — Anki parity review

Learning (`backend/learning/`) now schedules with FSRS. Still missing compared to Anki:

- ✅ Scheduler: FSRS (`backend/learning/scheduler.py`).
- ⬜ Configurable learning steps, lapses, leech handling.
- ⬜ Deck options: per-deck settings, daily new/review limits.
- ⬜ Card types: cloze deletions, reversed cards, multiple fields per note.
- ⬜ Media on cards (images, audio).
- ⬜ Import/export (`.apkg` compatibility would let existing Anki decks come over).

## Journal — drawing / handwritten pages

Pen input now exists as Paper (below), filed into the Journal feed at 4am.

- ✅ Multiple handwritten pages per day; ink layer usable with an Apple Pencil on iPad (shared ink layer, `src/components/ink/`), plus native PencilKit pages from the Apple app.
- ⬜ A standard **PDF planner template** copied for each day, with the handwriting on top of it.
- ⬜ Handwriting OCR so entries become searchable/taggable.

## Paper — handwritten pages

The Paper feature (`src/components/Paper/`, `backend/routes/paper.py`); the drawing itself lives in the shared ink layer (`src/components/ink/`).

**Platform limit to know about:** Apple Pencil's **double-tap and squeeze are not exposed to web browsers** by any API — they're native-app only. Tool switching on iPad therefore relies on the toolbar, a **two-finger tap** on the page, or a keyboard. Styluses with real hardware buttons (Wacom / Surface Pen / XP-Pen) _do_ report an eraser tip (`button 5`) and barrel button (`button 2`), and those are wired up.

### Correctness / must-fix

- ✅ **Real eraser** — geometric; splits the strokes it crosses and lays down nothing.
- ✅ **`onPointerLeave` ends the stroke** — now only ends it when pointer capture is not held.
- ⬜ **Stroke compaction.** Erasing no longer grows the payload, but pages still only get heavier as they're written on. Consider a binary or delta encoding, or gzipping the strokes blob, and cap the per-page size with a clear user-facing warning.
- ⬜ **Save conflict handling.** `PUT /api/paper/pages/<id>` is still last-write-wins for web ink (only native PencilKit pages compare revisions). Needs a server-side revision check like the newspaper markup's CAS.
- ⬜ **`list_papers` is N+1** — still two extra queries per paper, 60 papers per page of the grid. Fold into a single aggregate query.
- 🟡 **Upload size limits.** Some routes now set a per-route `request.max_content_length` (newspaper issues, mobile sync); Paper's routes and most others are still unbounded. The global `MAX_CONTENT_LENGTH` is deliberately left unset (`backend/app.py`).

### Features

- 🟡 **More ink**: ✅ colour selection with per-tool colour memory (four swatches in `InkToolPanel`); ⬜ straight-line/shape snapping; ⬜ lasso select + move.
- ⬜ **Ruled / grid / dot page backgrounds**, and a PDF planner template underlay.
- ⬜ **Zoom and pan** (pinch-zoom) on a Paper page, plus a wrist-guard / drawing-area offset. (The newspaper reader pinch-zooms natively; Paper's `exclusive` touch policy does not.)
- ⬜ **Title editing** — `PATCH /api/paper/<id>` accepts a title but no UI sets one.
- 🟡 **Page management**: delete exists; ⬜ reorder, duplicate, insert in the middle.
- ⬜ **Export** a paper to PDF or PNG.
- ⬜ **Handwriting OCR** so pages become searchable and taggable.

### Recently fixed

- Save payload exceeded Werkzeug 3.1's 500kB `max_form_memory_size` → `413`, which then made **every** navigation, the new-page button, and Back silently do nothing (the throw aborted them). Strokes now upload as a file part, `simplifyStroke` is actually applied (it was written but never called), save errors surface with a retry, and failures no longer block navigation.
- Added debounced autosave, an explicit "+ Page" button, revision-guarded save completion, and stylus/keyboard eraser toggling.
- The eraser became geometric and the stroke-splitting `onPointerLeave` was fixed when the ink layer was shared with the newspaper reader.

## Journal — views & tagging

- ⬜ View modes: **calendar view**, **list view**, and **day view** — the Journal is still a single feed.
- ⬜ **"On this day last year"** — show past entries from the same date in previous years.
- 🟡 Better tag coverage: curated tags already scan every existing entry when created; freeform AI tagging quality hasn't been revisited.

## Global search

- ⬜ One search box across everything: journal, emails, books, writing projects, files, todos. FTS5 now covers six content types (`journal_fts`, `recipes_fts`, `fic_chapters_fts`, `wiki_fts`, `emails_fts`, `messages_fts`) but each is searched only from its own view.
- ⬜ Blend keyword results with semantic search for the "I don't remember the exact words" case.

## AI reviews & check-in redo

- ⬜ **Weekly / monthly AI review**: summarize the period's journal entries, completed vs. slipped todos, and calendar; surface patterns. (The nightly briefing and the life wiki cover the day and long-term facts, not a period review.)
- ⬜ **Redo morning check-ins** (`stt/morning_checkin.py`) — the wake-detection flow is unchanged.

## Habit tracking

- ⬜ Habit grid: define habits, check them off daily. (Daily tasks — max 4, reset each day — are the nearest thing today.)
- ⬜ **Streaks** and simple stats over time.

## Lifestyle tab

Built — workouts, the activity heatmap, weight and progression trends, chores folded into todos, daily selfies, calories, and the Apple Health mirror. See [docs/lifestyle-tab.md](./lifestyle-tab.md) and `backend/lifestyle/CLAUDE.md`.

- ⬜ Selfie **timelapse export**.

## Study tab

Stages 1–3 are built: the split view, all three import kinds, the Notebook editor, and paper-style handwriting pages in the right half. Plan in [docs/study-tab.md](./study-tab.md).

- 🟡 **Resume position** — saved for PDFs (page) and YouTube (seconds); archived web pages store none, because the sandboxed iframe's scroll offset can't be read.
- ⬜ **Draggable divider** — the split is still fixed at half.

## Bug fixing

General stability pass — lots of small bugs to track down and fix. (Add specific known bugs here as they're identified.)

-

## Done

Sections built in full, kept here so the history of the list stays readable.

- **Library — saved books & fanfics.** XenForo threadmark scraping, epub/docx import, the library with folders and an in-app reader — `backend/fanfic/`, `src/components/Fanfic/`. Not built from the original list: AI summaries per book, reading-history stats.
- **Flashcards — spoken answers.** Answer aloud, transcribed, graded by the model and mapped onto the FSRS rating — `backend/ai/learning_grading.py`, `backend/learning/CLAUDE.md`.
- **Mobile / tablet access.** The web-first decision was reversed: there is now a native iPhone/iPad/Watch client in `apple/` with offline capture and sync (see the first section). The web app also has a manifest and queues recordings and paper pages in IndexedDB, but there is no service worker.
