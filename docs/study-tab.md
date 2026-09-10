# Study tab — staged build plan

**Status (2026-09-05): Stage 1 built, tested and committed on `feat/study-tab`** (`da45876`,
plus `cafb220` for the codec fix below). 28 backend and 21 frontend tests of its own; full suites
pass. Rebased onto `74a9a3b`, which added the rule that a new table is seeded in the same change —
`study_sources` now has three seed rows, so `./test-env.sh` brings up a Study tab with a real PDF,
a real archived article and a failed import to look at.

**One real YouTube import has been run, and it found a defect the green suite could not**: yt-dlp's
"best" is AV1, which Safari cannot decode on any 12.9" iPad Pro. Fixed. A PDF has still never been
rendered in a real browser, and nothing has been opened on the iPad itself — see
[What we do not know yet](#what-we-do-not-know-yet).

**Storage was corrected before Stage 2** (`feat/study-archive-videos`): downloaded videos now live on
the external archive drive and nowhere else, since `data/` is mirrored twice nightly and a lecture is
one `yt-dlp` away from being replaced. PDFs and archived pages stay on the SSD and keep being backed
up. See [Where the bytes live](#where-the-bytes-live).

**Stage 2 is built** (`feat/study-resume-position`): one `position REAL` column, a page for a PDF and
seconds for a video, restored on open and shown in the library.

**Stage 3 is built** (`feat/study-handwriting-pane`): the right half toggles between the Notebook
editor and a handwriting paper, and the source remembers which one it was last studied with.

This is the timeline. `backend/study/CLAUDE.md` documents how the built code works and what will
bite someone changing it; this documents what is coming, in what order, and why that order.

## Why the tab exists

There was nowhere to _study from_ something. Books, saved articles and lecture videos were either
not in the app at all or scattered across Files, Fanfic and Notebook, and taking notes on one meant
alt-tabbing between two windows.

Study is a reading desk: the thing you are learning from on the left, your notes on the right,
permanently side by side. It is deliberately large-screen-only — a two-pane desk on a 7" Pocket 2
screen is worse than not having the tab.

## Where the stages come from

The scope was dictated in one pass and explicitly cut short: _"let's build this beginning part out
first, and then I'll continue on."_ So the stages below split into three kinds, and the doc marks
which is which rather than presenting them as one plan:

- **Sourced** — stated in the original brief and deferred by it.
- **Deferred by a decision** — offered during planning, consciously declined for now.
- **Unsourced** — candidates nobody has asked for. Listed at the bottom, _not_ in the timeline.

---

## Stage 1 — the desk ✅ built

The half that had to exist before any of the rest is worth anything.

- **Sources**: upload a PDF, archive a website as sanitized HTML, download a YouTube video via
  `yt-dlp`. Bytes under `<STUDY_ROOT>/<source_id>/`, path-only in the row.
- **Desk**: fixed 50/50 vertical split. Left is the source; right is the Notebook vim editor bound
  to a `.md` note.
- **Gate**: hidden below 1024px — absent from the sidebar and skipped by `nav.up/down`.
  _Superseded_ — see [Everywhere, but smaller](#everywhere-but-smaller).

Three decisions worth carrying forward:

1. **Notes are Notebook files, not a new store.** The right half mounts the existing
   `NotebookEditorPane`, so `:w`, `:find`, `[[wiki links]]` and the offline-aware save came for
   free, and a study note is reachable from the Notebook tab like any other. This is the
   interpretation of _"a text editor that you can import from the notebook tab"_ — see
   [Open questions](#open-questions), it may not be what was meant.
2. **`largeOnly` is a different gate from Piano's `desktopOnly`.** The Pocket 2 satisfies the
   native-shell one. Both now run through one pure `visibleNavItems`. _(`largeOnly` has since been
   removed — the size question moved inside the view. `visibleNavItems` stays.)_
3. **The web import reuses the SSRF guard by factoring, not copying.** `fetch_public_page` is now
   the guarded fetch on its own and `web_fetch` is that plus the strip — one redirect loop in the
   codebase.

**Schema already carries the next two stages.** `study_sources` has `duration_seconds`,
`last_opened_at` and room beside them; nothing in Stage 2 or 3 needs a table rebuild.

---

## Where the bytes live

Not a stage — a correction made after Stage 1 was used for real, and the answer to the second of the
[open questions](#open-questions) about what "save system" covers.

- **`./data/study/<id>/`** — an uploaded PDF or an archived page. Small, and an uploaded PDF has no
  source URL, so losing it means losing it. Backed up exactly like everything else under `data/`.
- **`<settings.backup_path>/archive/study/<id>/`** — a downloaded video, and this is its **only**
  copy. It is not backed up, because the archive _is_ where it lives. `ops/backup.sh` needed no
  change: the archive is a sibling of the rsync destination and outside its source.

Two consequences that are decisions, not bugs. With the drive unplugged, **an import fails loudly**
rather than falling back to the SSD (a `mkdir -p` onto an unmounted mountpoint followed by a 279 MB
download is the one failure that looks like success), and **a video already imported is listed but
cannot be played** — Piano's model, with the desk saying why.

---

## Everywhere, but smaller

Not a stage — the second correction that came out of using the thing, and the answer to Stage 1's
`largeOnly` gate.

Hiding the tab below 1024px treated Study as one feature. It is two: **reading** something beside
your notes, which genuinely needs two panes, and **collecting** the thing in the first place, which
needs a text field and is most wanted on the phone you found the link on. The gate took the second
away along with the first, so a YouTube lecture spotted on a phone had to be remembered until you
were back at a big screen.

So the size question moved out of the nav list and into the view:

- **`largeOnly` is gone from `navVisibility.ts`.** Piano's `desktopOnly` is the only nav gate left —
  a gate with no users rots, and this one now has none. "Which tabs exist" is a question about the
  device; "how much of this tab works" is a question about the window, and they were being answered
  in the same place.
- **Below 1024px, Study is `StudyLibrary` alone**: upload a PDF, archive a page, pull down a video,
  see what is importing, retry a failure, delete. Everything except opening one to read.
- **A row there is a `<div>`, not a disabled button.** On a phone every row would be a control that
  looks pressable and is not.
- **Narrowing the window closes an open desk** rather than hiding it behind the library — a desk
  held in state is a source the library can meanwhile delete.
- **The delete `✕` no longer waits for a hover**, which a touch screen cannot give it. It was
  `opacity-0 group-hover:opacity-100`: invisible on exactly the devices this change is for.

---

## Stage 2 — the save system ✅ built _(sourced; called "a fairly big thing" in the brief)_

**Resume where you left off.** Named in the original brief and deferred in the same sentence — the
stage that turns Study from a viewer into somewhere you actually study, because the cost of closing a
400-page PDF or a two-hour lecture was "find your place again by hand."

- **One column, `study_sources.position REAL`.** Its meaning comes from the row's existing `kind`: a
  page number for `pdf`, seconds for `youtube`. A `position_kind` column would only repeat what
  `kind` already answers. **Articles store nothing** — the archived page renders inside
  `<iframe sandbox="">`, an opaque origin whose scroll offset cannot be read or set from outside, and
  reaching in would weaken the layer that frame is there to be.
- **Written cheaply and often**: debounced 1500 ms, client-driven, fire-and-forget, and deliberately
  not invalidating any query. Flushed on leaving the desk. It does **not** bump `updated_at`.
- **Restored on open**: a `pendingPageRef` consumed inside pdf.js's render loop as the matching
  canvas is appended (the canvas does not exist at mount), and `loadedmetadata` for a video.
- **Shown in the library**: `page 214`, `47 min in` — most of why the position is worth storing is
  seeing it without opening the thing.

The one thing that changed from the plan while building: zooming a PDF rebuilds every canvas and
sends the scroller to the top, so the pending page is **re-armed on a zoom change**. Without that,
zooming reported page 1 and overwrote the stored position with it.

---

## Stage 3 — paper pages on the right ✅ built _(sourced)_

**A handwriting page as an alternative to the text editor.** From the brief: the right side is
_either_ a vim editor _or_ "a paper style page", and — unlike the editor, which "can have unlimited
length, so there's no need for more pages" — the paper side needs "the opportunity to create new
pages."

- **A toggle, not a replacement.** `⌨ Notes` / `✎ Paper` in the desk header; `study_sources.note_mode`
  remembers the choice, and like `position` it does **not** bump `updated_at` — which pane you had
  open is not an edit to the source.
- **A whole paper, borrowed.** `paper_id` is an ordinary `papers` row, so `＋ Page`, prev/next, the
  tool palette, pictures and the manual-save contract all came with it, and the paper is listed in
  the Paper tab like any other. `ON DELETE SET NULL`: deleting it there costs the source its
  binding, not its existence. Made lazily on the first switch, exactly as the note is.
- **The editor gained an `embedded` mode**, which is two subtractions (no `‹ Back`, no "to journal")
  and one addition (`PaperEditorHandle.commitLocal`).

Both halves of the tension the plan flagged were resolved, and neither the way the plan guessed:

- **Immersive mode is now two claims, not one.** A full claim would have hidden the sidebar while a
  PDF was being read on the _other_ half of the split. On the screens Study runs on the header is
  already `md:hidden` and the sidebar is already a left-hand column, so the only chrome actually
  crossing under the page is the bottom Transcribe/Journal/Record strip — and `useHideBottomBar`
  takes just that. Worth about 5% more page height on a 12.9" iPad; the sidebar stays.
- **Manual save survives unchanged**, but the desk had to learn to commit. `PaperEditor` has no
  unmount commit and **cannot** have one: a passive effect's cleanup runs after React has detached
  the canvas ref. In the Paper tab that was invisible, because `‹ Back` is the only way out and it
  commits. In the desk both `‹ Sources` and the mode toggle unmount the editor, so `StudyDesk` calls
  the handle first — without it, up to two seconds of ink went missing on the way out.

---

## Stage 3.5 — into the Journal ✅ built _(sourced, 2026-09-09)_

**A source is filed into the Journal the way a paper is, and its card carries the sitting whole.**

- `study_sources.archive_requested_at`, the same flag and the same lazy 4am move papers use
  (`_cutoff_4am` over `backend/day_boundary.py`; no scheduler, correct after a restart). The library
  and `GET /api/study/journal` are exact complements, so a filed source is in one or the other and
  never both — filing it takes it out of the library, which is what "goes to the Journal" means here.
- The toggle is in **both** the desk header and the library row. The desk is ≥1024px only, so the row
  is the only way to file from the phone, and it is deliberately not hover-revealed the way `✕` is.
- **One card, three parts: the source, the bound paper's pages, the Notebook note.** Reading the
  article and writing the page beside it are not two events in the day's record. The consequence is
  that a bound paper gets no card of its own (`journal_papers()` excludes it), and `PaperEditor`
  hides its To-journal button when `paper.studySourceId` is set — otherwise filing it there would
  take it out of the explorer and produce no card anywhere.
- The media is a heading and a link, not a thumbnail: `yt-dlp` saves no poster and an archived page
  renders in a `sandbox=""` iframe nothing outside it can read. A video on an unplugged drive still
  gets a card, listed and unreachable, as `viewerKindFor` already treats it.
- **The card lands at the last time the source was worked on**, not at the flag
  (`backend/journal_moment.py`): the newest of the bound paper's ink and the note file's mtime,
  falling back to `last_opened_at`. `study_sources.updated_at` is deliberately not consulted — `touch`
  bumps it on every open, which would place the card at the moment you sat down. Clamped into the
  filed day, because the card is view-only.

---

## Stage 4 — the divider _(deferred by a decision, 2026-09-04)_

**A draggable, remembered split.** Offered during planning; 50/50 was chosen instead, with dragging
noted as addable later.

- Drag math belongs in `src/lib/` as a pure tested module, beside `paperImages.ts` and
  `calendarDayLayout.ts`.
- Ratio persists per device in `localStorage`, not in the DB.
- Would be the first resizer anywhere in the app.

**Why last of the planned stages:** it is comfort, not capability, and it is the only stage that
changes nothing about what the tab can do.

---

## Not scheduled, not requested

Candidates that came up while building or that the shape suggests. **Nobody has asked for any of
these** — they are here so they are not re-derived from scratch, not because they are planned.

- **Highlighting / annotation** on a PDF or article, with the highlight quoted into the note.
- **Handwriting or text OCR** so a source's contents reach Global search — already a live want for
  Paper (see ROADMAP.md), so it would be one mechanism serving both.
- **Flashcards from a source**, feeding the Learning tab's FSRS scheduler.
- **Transcript for a downloaded video** — the Whisper pipeline in `backend/meetings/` already
  exists, and a searchable, seekable lecture transcript is a small step from it.
- **A source in the Ideas / chat context**, so the assistant can answer about what you are reading.

## Open questions

Four things the brief left genuinely ambiguous; two are now settled. The first should be settled
before Stage 3.

1. **"a text editor that you can import from the notebook tab"** — Stage 1 read this as _notes are
   Notebook files_. It could instead have meant _pull a copy of a note in, edited separately_. The
   built behaviour is the more useful reading, but it is a reading.
2. ~~**What "save system" covers.**~~ **Settled.** It means reading position; durability was
   answered separately and in the opposite direction — see
   [Where the bytes live](#where-the-bytes-live). Videos are deliberately _not_ durable.
3. **The 1024px threshold is a guess** at the Pocket 2's CSS width, which depends on its OS
   scaling. One constant in `src/lib/breakpoints.ts`. A 12.9" iPad in _portrait_ reports exactly
   1024, so the boundary is tight on purpose. Less costly to get wrong now than it was: below the
   line the tab still exists, so a bad guess loses the desk rather than the whole feature.
4. **Whether a source should ever be deleted automatically.** Downloaded lectures are the largest
   files the app stores. Jobs has a retention sweep; nothing here does, by choice for now.

## What we do not know yet

Honest list of what Stage 1's green suite does _not_ prove:

- ~~**The real `yt-dlp` path has never run.**~~ **Run on 2026-09-05, and it caught a real defect
  the green suite could not.** The mechanics were fine — correct title and duration, the height
  cap held, no leftover `video.fNNN.mp4` fragments, and a Range request returned
  `206 Partial Content`, so seeking genuinely works. But yt-dlp's idea of "best" is **AV1 + Opus**,
  and Safari has no software AV1 decoder; Apple's first hardware one is the A17 Pro / M3, so every
  12.9" iPad Pro fails to play it silently. The tab is large-screen-only _for that iPad_, so the
  feature was unusable on its own target device while every test passed. Fixed by naming
  H.264 + AAC in the selector. **The lesson generalises: a stubbed subprocess proves the arguments
  we pass, never what comes back.**
- **No PDF has been rendered in a real browser** — pdf.js is mocked in the component test. The
  build emits the worker chunk correctly, which is a different claim.
- **Nothing has been read on an actual iPad**, which is half the reason the tab is gated to large
  screens at all.
- **No paper has been written on inside the desk.** Stage 3's tests mock `PaperEditor` deliberately
  — what they check is the desk's half of the contract (which pane, what it tells the server, that
  it commits before unmounting), and `PaperEditor.test.tsx` covers the editor's own 1,400 lines. The
  join has never been exercised with a real stylus at half width, and two things about it are only
  arguments so far: that an A4 page contain-fitted into ~683pt is still usable to write on, and that
  the toolbar (which scrolls sideways rather than wrapping) is reachable there. Both are answerable
  in five minutes on the iPad and by nothing else.
- **The real archive drive has never been written to, or unplugged.** `feat/study-archive-videos`
  exists to stop a video import filling the root partition when the drive is absent, and the test
  that proves it uses a fake root under `tmp_path` — so what is verified is the logic, not the
  drive at `/media/expansion/lunaschal`. Two things are still worth doing by hand, in this order:
  import a short video and confirm the bytes land under `<backup_path>/archive/study/` and **not**
  under `data/study/`, then unplug the drive and confirm the next import fails loudly rather than
  quietly succeeding onto the SSD. The second is the whole point of the branch, and it is exactly
  the shape of failure the AV1 defect was — green suite, broken behaviour, and only a real run
  can tell the difference.
