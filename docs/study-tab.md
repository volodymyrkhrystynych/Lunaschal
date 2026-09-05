# Study tab — staged build plan

**Status (2026-09-05): Stage 1 built and tested, uncommitted, on `feat/study-tab`.** 27 backend
and 21 frontend tests added; the full suites pass (14/14 backend batches, 1730 frontend tests).
Stage 1 has **not** been run against a real `yt-dlp` download or a real PDF in a browser — the
tests stub the subprocess and mock pdf.js. Stages 2 onward are not started.

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

Three decisions worth carrying forward:

1. **Notes are Notebook files, not a new store.** The right half mounts the existing
   `NotebookEditorPane`, so `:w`, `:find`, `[[wiki links]]` and the offline-aware save came for
   free, and a study note is reachable from the Notebook tab like any other. This is the
   interpretation of _"a text editor that you can import from the notebook tab"_ — see
   [Open questions](#open-questions), it may not be what was meant.
2. **`largeOnly` is a different gate from Piano's `desktopOnly`.** The Pocket 2 satisfies the
   native-shell one. Both now run through one pure `visibleNavItems`.
3. **The web import reuses the SSRF guard by factoring, not copying.** `fetch_public_page` is now
   the guarded fetch on its own and `web_fetch` is that plus the strip — one redirect loop in the
   codebase.

**Schema already carries the next two stages.** `study_sources` has `duration_seconds`,
`last_opened_at` and room beside them; nothing in Stage 2 or 3 needs a table rebuild.

---

## Stage 2 — the save system _(sourced; called "a fairly big thing" in the brief)_

**Resume where you left off.** Named in the original brief and deferred in the same sentence. This
is the stage that turns Study from a viewer into somewhere you actually study, because the cost of
closing a 400-page PDF or a two-hour lecture is currently "find your place again by hand."

Scope, in the order it should be built:

- **Position per source.** A page number for a PDF, a timestamp for a video, a scroll offset for an
  article. One nullable column plus a small `position_kind`, or a single JSON blob — the shapes are
  different enough that one integer will not do.
- **Write it cheaply and often.** Debounced, client-driven, fire-and-forget — this is the one write
  in the feature where losing the last few seconds costs nothing. It must **not** go through the
  save path notes use.
- **Restore on open.** pdf.js already renders page-by-page with a real page number precisely so
  this has something to hang on; the `<video>` element needs `currentTime` set once metadata loads.
- **Show it in the library.** "page 214 of 380", "47 min in" — a progress hint is most of why the
  position is worth storing at all.

**Why second and not third:** it is the only deferred item the brief called out itself, it is
cheap, and it compounds with everything after it. Paper pages on a source you cannot resume is a
nicer version of a thing you still avoid opening.

**Open:** whether "save system" also meant _the source archive itself_ (a durable library that
survives a reinstall) rather than reading position. See [Open questions](#open-questions).

---

## Stage 3 — paper pages on the right _(sourced)_

**A handwriting page as an alternative to the text editor.** From the brief: the right side is
_either_ a vim editor _or_ "a paper style page", and — unlike the editor, which "can have unlimited
length, so there's no need for more pages" — the paper side needs "the opportunity to create new
pages."

- **A toggle, not a replacement.** Both note modes live; the source remembers which one it was last
  studied with.
- **Reuse `paper_pages`, do not re-model.** There is already precedent: `idea_sketches` borrows a
  single `paper_pages` row rather than owning storage. Study should borrow a whole `paper` (a
  document with ordered pages), since page creation is the explicit requirement.
- **`＋ Page` and prev/next**, matching `PaperEditor`'s existing navigation.

**Why third:** it is the largest of the sourced stages by some margin. `PaperEditor` carries its
own manual-save contract, IndexedDB page store, staged-picture handling and immersive mode, and
`backend/paper/CLAUDE.md` documents a long list of ways that machinery bites — a half-width paper
pane inside another view is new ground for all of it. Doing it after Stage 2 means the cheap win
ships first.

**Known tension to resolve before starting:** Paper's immersive mode hides the app chrome on a
coarse pointer, and Paper never syncs except on an explicit Save. Neither behaviour obviously
survives being one half of a split view. This needs a decision, not a discovery mid-build.

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

Four things the brief left genuinely ambiguous. None block Stage 2; the first two should be settled
before Stage 3.

1. **"a text editor that you can import from the notebook tab"** — Stage 1 read this as _notes are
   Notebook files_. It could instead have meant _pull a copy of a note in, edited separately_. The
   built behaviour is the more useful reading, but it is a reading.
2. **What "save system" covers.** Read here as reading position. It may have meant durability of
   the archive itself, which is a different (and larger) piece of work touching `ops/backup.sh`.
3. **The 1024px threshold is a guess** at the Pocket 2's CSS width, which depends on its OS
   scaling. One constant in `src/lib/breakpoints.ts`. A 12.9" iPad in _portrait_ reports exactly
   1024, so the boundary is tight on purpose.
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
