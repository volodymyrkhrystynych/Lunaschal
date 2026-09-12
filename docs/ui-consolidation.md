# UI consolidation audit

An audit of `src/components/` for interaction patterns that got reimplemented independently in
each feature instead of sharing one component. Scope excludes recording/transcription UI (owned
separately). Findings below are grouped by pattern, with file:line evidence, and end in a
prioritized list of components to extract.

No shared primitives exist today for any of these five patterns — each is copy-pasted per
feature, sometimes with small drift (different wording, different corner radius, different close
behavior) that a shared component would also fix as a side effect.

## 1. Tag / filter pills and status badges

Same rounded-pill, active/inactive class-toggle button, reimplemented byte-for-byte:

- `Journal.tsx:1033` — curated tag filter buttons
- `Journal.tsx:1047` — "Show transcriptions" toggle, same class string reused for a non-tag control
- `Food/FoodLog.tsx:363` — food tag filter, identical classes, `#{tag.name} (count)`
- `Food/RecipeList.tsx:436` — recipe tag filter, identical classes
- `Fanfic/Library.tsx:284` — folder-view segmented buttons, same shape
- `Fanfic/Library.tsx:304` — active-tag "clear filter" chip
- `Fanfic/Folders.tsx:7` — `const pillBase = 'px-3 py-1 text-sm rounded-full border transition-colors'` (already extracted locally, not shared)
- `Learning/Learning.tsx:25` — `export const pillClass = (active) => ...`, same shape, exported but only used inside Learning
- `Calendar/index.tsx:383` — same active/inactive toggle logic, but square-cornered (`rounded`, not `rounded-full`)

Two local files (`Fanfic/Folders.tsx`, `Learning/Learning.tsx`) already tried to DRY this on their
own — a signal the shared extraction is overdue.

Lighter, non-clickable display chips, a related but distinct sub-case:

- `Learning/Queue.tsx:139`, `Learning/Browse.tsx:128` — `#{t}` chips (Browse's is clickable, Queue's isn't)
- `Calendar/EventDetails.tsx:327` — static `tags` chip, square-cornered
- `Calendar/EventDetails.tsx:341` — `categoryTags` chip with a per-category inline hex color — genuinely different (color-keyed, not class-toggle), don't force into the pill component

Status badges are a separate concern (state indicator, not a filter), each with its own
status→class/label map:

- `Meetings.tsx:285` — `styles`/`labels` maps keyed by `Meeting['status']`
- `Jobs/Feed.tsx:432,443` — triage flag/commute badges, same map-driven idea, squarer corners

**Extract:** `TagPill` (`active`, `onClick`, `label`, `count?`, optional `#` prefix) for the first
group. `StatusBadge` (`status`, `labelMap`, `colorMap`) for Meetings/Jobs. Leave Calendar's
per-category hex chip and the plain static tag list alone, or at most let them reuse `TagPill`'s
markup via a `style` override — not its active-state logic.

## 2. Modals, image lightboxes, and delete confirmation

`ImageLightbox.tsx` exists and is documented as shared by "paper/food/journal-attachment
filmstrips," but it's actually only imported by `Journal.tsx` and `JournalAttachments.tsx`. Food
and Ideas reimplemented their own instead:

- `Food/FoodLog.tsx:414` — `fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4`, click-to-close — near-identical to `ImageLightbox`
- `Ideas/SketchStrip.tsx:120-127` — same `z-50 bg-black/80` + `object-contain` pattern, no close button, backdrop-click only

Paper/Study/Newspapers have no lightbox at all, despite being named in the code comment as
consumers.

Generic modal/overlay shell (`fixed inset-0 z-50` backdrop + centered panel + `stopPropagation`),
each hand-rolled, no shared `Modal`:

- `Ideas/SketchPicker.tsx:33`
- `Settings/FolderPicker.tsx:56`
- `Fanfic/BookmarkMenu.tsx:21`
- `NoteReview.tsx:91`
- `Sidebar.tsx:167` (mobile drawer — different animation/positioning, treat separately)

Delete confirmation is three divergent UX patterns with no shared `ConfirmDialog`:

1. `window.confirm(...)`: `NewspaperReader.tsx:663`, `Piano/index.tsx:257`, `Fanfic/Folders.tsx:218`, `Fanfic/Library.tsx:476`, `Editor/PendingRecordings.tsx:71`, `Torrent/TorrentList.tsx:245`
2. Bare `confirm(...)`: `Ideas/IdeaDetail.tsx:278`
3. Custom state-machine step (`Calendar/EventDetails.tsx`, `mode: 'confirmDelete'`, lines 36, 433-467) — genuinely different because of the recurrence-scope question it also asks; keep custom
4. No confirmation, a toggle-visible delete button instead: `Learning/Browse.tsx:103`, `Learning/Folders.tsx:83`, `Tasks/TodoSection.tsx:50`, `Learning/Queue.tsx:300`, `Tasks/DailyTasks.tsx:128-141`

**Extract:** merge `Food/FoodLog.tsx:414` and `Ideas/SketchStrip.tsx:120-127` into the existing
`ImageLightbox`, and check whether Paper/Study actually want image preview (may just be a doc-comment
lie). Extract a `Modal` shell for the five overlay call sites (drawer stays separate). Extract a
`ConfirmDialog` for the six `window.confirm` sites; leave `EventDetails`'s recurrence-aware step
alone; leave the toggle-delete UX as a deliberate lighter-weight alternative rather than forcing it
into a dialog.

## 3. List rows and item cards

**Cluster A — "surface-panel card"**: bordered `bg-[var(--color-surface)]` box, title + meta stack,
trailing action-button row. Same shape in four places:

- `Fanfic/Library.tsx:546-634` (`FicCard`) — cover image, title, right-aligned action cluster, meta row
- `Jobs/Feed.tsx:401-524` — title/subtitle, flag badges, progress bar, action buttons
- `Journal.tsx:689-790` (`renderFeedItem`, dispatching `JournalPaperItem`/`JournalFoodItem`/`JournalStudyItem`/`JournalNewspaperItem`/`JournalTaskEventItem`) — five near-duplicate inline card renders switched on `item.kind`
- `Torrent/TorrentList.tsx:180-222` — title/status row, progress bar, meta line, actions row, as an `<li>` instead of a `<div>`

`src/lib/journalFeed.ts` already normalizes disparate journal sources into one `FeedItem` union —
the data-layer half of this abstraction exists; the visual half (`renderFeedItem`'s five branches)
doesn't.

**Cluster B — "compact clickable list item"**: leading control + title/subtitle stack + trailing
chip/buttons, selection via border/bg swap:

- `Email/EmailList.tsx:108-149` — whole row is a `<button>`
- `Ideas/IdeaList.tsx:144-214` — whole row is a `<button>`, already reuses `Settings/CollapsibleSection` correctly elsewhere in the same file
- `Tasks/TodoRow.tsx:53-121` — checkbox and inline-edit are separate nested interactive targets, can't be a single `<button>` wrapper
- `Study/StudyLibrary.tsx:180-262` (`RowBody`) — deliberately a non-button div for unopenable rows, `min-h-[44px]` touch sizing
- `Learning/Folders.tsx:55-97,194-237` — icon + name + trailing actions, simpler (no subtitle line), a partial fit

Email/IdeaList (row-as-button) and TodoRow/StudyLibrary (row-with-nested-controls) are two
different interaction models wearing the same visual clothes — merging them needs the shared
component to abstract "row is a button" vs. "row hosts its own controls," not just share CSS.

**Cluster C — dashboard stat cards** (`Lifestyle/CaloriesCard.tsx`, `WeatherCard.tsx:82-145`,
`SelfieCard.tsx:147-260`) are single-instance widgets, not list items. Share visual vocabulary
only; not a list-row merge candidate.

**Not actually list-row cases** (confirmed, drop from scope): `Notebook/Notebook.tsx` (editor pane,
no rendered list), `Fanfic/Folders.tsx:101-141` (pill-row, not title+meta+actions),
`Tasks/TodoSection.tsx:82-125` (container, not a row itself).

**Extract:** `ItemCard` (slots: `thumbnail?`, `title`, `titleActions?`, `meta`, `progress?`,
`body?`, `actions`) for Cluster A — start with `Journal.tsx`'s five-way `renderFeedItem` switch,
since the data normalization is already there. `ListRow` (leading slot, title, subtitle, trailing
slot, `variant: 'button' | 'controls'`) for Cluster B, built to support both interaction models
from day one rather than retrofitted.

## 4. Loading, empty, and error states

No `LoadingState`/`EmptyState`/`ErrorBanner` primitive exists anywhere in the frontend.
`src/hooks/api.ts` is a plain fetch wrapper with no centralized error/loading UI — every view rolls
its own from raw React Query state.

Duplicated loading markup, identical except one outlier:

- `Meetings.tsx:196`, `Settings/index.tsx:54`, `Journal.tsx:1088`, `Food/RecipeList.tsx:641`, `Food/FoodLog.tsx:378`, `Fanfic/Library.tsx:458`, `Calendar/EventDetails.tsx:167` — all `<div className="text-[var(--color-text-muted)]">Loading...</div>`
- `Notebook/Notebook.tsx:178` — different ellipsis character (`…` vs `...`) and its own error copy

Duplicated empty-state copy, each hand-written and inconsistently worded:

- `Study/StudyLibrary.tsx:172`, `Fanfic/Library.tsx:488`, `Learning/Browse.tsx:153`

Duplicated error banners, each with slightly different markup and inconsistent `role` usage:

- `Journal.tsx:1438-1443`, `Jobs/Feed.tsx:527`, `Learning/BrainDump.tsx:119-124`, `Meetings.tsx:370-374`, `CuratedTagsSection.tsx:93`, `Settings/ReposSection.tsx:96`, `Login.tsx:99`, `Knowledge/Knowledge.tsx:91,113,133` (three in one file), `Jobs/ApplicationDetail.tsx:247,378,479,505,718,792` (six in one file)
- Some use `role="alert"`/`role="status"` (`Newspapers.tsx`, `Food/FoodDescriptions.tsx`), most don't

`InferencePausedBanner.tsx` and `OfflineIndicator.tsx` are the one place this is done well —
global, dismissible-by-condition, mounted once at the shell — but nothing per-view reuses that
shape; every per-view "failed to load"/"AI unavailable" case is inlined instead.

**Extract:** `LoadingState` (standardized copy/className, inline vs. full-panel variant),
`EmptyState` (`title`, `message?`, optional `action: {label, onClick}`), `ErrorBanner` (`error:
unknown`, `onRetry?`, `role?`) that normalizes `Error`-vs-string-vs-React-Query-error extraction
and matches the existing banner strip's visual language rather than the ad hoc red-400 text.
`Jobs/ApplicationDetail.tsx` alone would collapse six call sites into one.

## 5. Collapsible sections, inline edit, and tag-input forms

`Settings/CollapsibleSection.tsx` exists and is used ~15 times inside Settings, but every other
feature area reimplemented the same disclosure interaction instead of reusing it:

- `Fanfic/Library.tsx:523,573-577,678` — own `useState` + `▾/▸ Details` toggle, manual `aria-expanded`
- `Jobs/Feed.tsx:389,490,532-535` — same pattern, `Read posting`/`Less`
- `Jobs/SteerBar.tsx:31,73-107` — separate expand toggle, chevron logic duplicated again
- `Food/RecipeList.tsx:181,362,647,660,783-784` — per-row `expandedId` state, same "click header, reveal body" job

`Ideas/IdeaList.tsx` already reuses `CollapsibleSection` correctly — proof the component
generalizes fine outside Settings, it's just not promoted or discovered.

Inline edit-in-place ("click text → autofocus input → blur/Enter/Escape saves"), copy-pasted
verbatim:

- `Tasks/DailyTasks.tsx:181-204`
- `Tasks/TodoRow.tsx:77-90+`

Same `autoFocus`, same `onBlur={saveEdit}`, same Enter/Escape handling, same Tailwind classes.
`CuratedTagsSection.tsx:103-140` is a related but distinct sub-pattern — explicit Save/Cancel
buttons instead of blur-to-save.

Tag/comma-separated input forms — four independent implementations of roughly the same job:

- `Jobs/ProfileEditor.tsx:7-49` — its own `Field` draft/commit component, reused at `:260-271` for the blacklist field
- `Food/RecipeList.tsx:16,278,371,771` — its own `splitTagInput`/`parseTags` plus its own edit-mode form
- `CuratedTagsSection.tsx:54-91` — its own create-form-with-Enter-to-submit
- (`Calendar/EventFormFields.tsx`, `Tasks/TodoForm.tsx` have no tag fields — not a hit)

**Extract:** promote `CollapsibleSection` out of `Settings/` into a shared location and point
Fanfic/Jobs/SteerBar/RecipeList at it. Extract `InlineEditableText` (blur/Enter/Escape-to-save) for
`DailyTasks`/`TodoRow`. Lower priority: a `TagsInput` (comma-split, chip rendering) to unify
RecipeList, ProfileEditor's blacklist field, and CuratedTagsSection's create form.

## Priority order for extraction

Ranked by (call-site count × how identical the copies already are), not by feature importance:

1. **`TagPill`** — 7+ byte-for-byte duplicate call sites, two features already tried to DRY it locally
2. **`CollapsibleSection` promotion** — component already exists and works outside Settings (Ideas proves it), this is discovery/wiring, not new code
3. **`ItemCard`** — `Journal.tsx`'s five-way card switch is the highest-value single fix; `journalFeed.ts` already did the data-side work
4. **`LoadingState` / `EmptyState` / `ErrorBanner`** — highest call-site count (20+) but lowest risk per site; `Jobs/ApplicationDetail.tsx` alone justifies it
5. **`ImageLightbox` consolidation** — only 2 call sites to fold in, small and safe
6. **`ConfirmDialog`** — 6 `window.confirm` call sites, safe replacement, low behavior risk
7. **`InlineEditableText`** — 2 call sites today, but exact duplicates
8. **`ListRow`** — real value but needs the button-vs-nested-controls interaction split designed carefully; do last
9. **`Modal` shell**, **`StatusBadge`**, **`TagsInput`** — smaller wins, bundle in wherever convenient

Each of these is small enough to do as its own PR/branch per this repo's branch-per-feature
convention, and each should land with a Vitest component test per the usual testing bar rather
than relying on manual click-through.
