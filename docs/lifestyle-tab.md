# Lifestyle tab — design doc

**Status: built.** Sections 1–6 ship in `backend/routes/lifestyle.py`, `backend/lifestyle/`,
`backend/ai/workouts.py` and `src/components/Lifestyle/`; the timelapse export (the doc's
explicit later phase) is still not built. This document is kept as the design record — where the
implementation settled a question the doc left open, it's noted inline below.

It captures the plan for a top-level "Lifestyle" view covering workout tracking, a
GitHub-commits-style activity heatmap, daily selfies, chores, body weight, and (lightly)
calorie tracking.

## What the build settled

- **Chores reuse the existing list.** They were already todos with `list='chores'`
  (`backend/todo_recurrence.py`), so the Lifestyle section renders those same rows via
  `/api/tasks/todos` instead of the parallel `chores` / `chore_completions` tables sketched
  below. Ticking a chore off here and in Tasks is one action, and completions still reach the
  Journal feed through `task_events`. **Superseded — see "The tab absorbed Tasks" below:** the
  chores list is retired entirely, and the rows are plain to-dos.
- **Exercise canonicalization folds one way only.** An abbreviation folds onto a known fuller
  name ("curls" → "bicep curl"), but a _more specific_ new name starts its own series, because
  folding "hack squat machine" into "squat" would silently destroy a real distinction.
  Over-merging is unrecoverable; under-merging costs one `POST /api/lifestyle/exercises/merge`.
  See `backend/lifestyle/exercises.py`.
- **No charting library.** Two sparklines didn't justify a dependency — the geometry is pure
  functions in `src/lib/lifestyle.ts` (`plotSeries`, `nearestPoint`), rendered as inline SVG by
  `src/components/Lifestyle/Sparkline.tsx`, and unit-tested in the node environment.
- **Both progression metrics ship.** `/progression` returns top-set weight _and_ volume per day
  so the chart toggles between them rather than the choice being made blind.
- **Heatmap shading defaults to duration**, with an intensity toggle — both are logged on every
  session from day one, as the doc asks, so the question stays open on real data.
- **Calorie entry is parsed locally, not by the LLM.** "chicken and rice, ~600" splits with a
  regex (`parseCalorieEntry`); the format is regular, and a calorie note shouldn't wait on a
  model. Note the pre-existing **Food tab** is the richer photo/review log — this is deliberately
  the lightweight counter beside it.
- **The PWA caveat is already handled** — Lunaschal ships an installed PWA (`public/manifest.json`),
  which is why the localStorage draft save is written as the actual fix rather than a stopgap.
- **Selfie capture hands off to the device camera.** The first build paired a `getUserMedia` live
  preview with a `capture="user"` file input; on an iPad the in-page `<video>` was plainly the
  worse camera, so both buttons now take the native path (front camera vs. photo library) and the
  preview machinery is gone. Nothing in the thumbnail strip deletes any more either — a tap used
  to delete the day's selfie and cost real photos. Replacing a day means retaking it that day;
  deleting is a deliberate database operation.
- **Intensity is 1–5 stars with written meanings, not a 1–10 RPE.** Ten points were too subjective
  to rate the same way twice. Each star says what it means ("Not intense whatsoever" … "I am going
  ham"), surfaced as the picker's label and every readout's tooltip/screen-reader text, because
  the words are the whole point. Stored 1–10 values were folded once by
  `_migrate_workout_intensity_to_stars` (ceil(v/2), guarded by a marker column — the fold is only
  correct on un-migrated data).
- **Bodyweight sets carry a null weight, never 0.** "squats 10 10 10 10" is four sets of ten with
  nothing loaded; the parse schema requires an explicit `"weight": null`, the log renders it as
  "10 × 4 bodyweight", and an exercise that was never loaded charts total reps instead of an empty
  weight line.

## Why a new tab

Workouts, body weight, and daily selfies don't fit anywhere that exists today. Chores currently
live as a secondary list inside Tasks and deserve a more prominent home. Bundling all of these
under one "Lifestyle" tab keeps the sidebar from growing a separate entry per small feature.

## Sections within the tab

1. **Activity heatmap** (front and center — see below)
2. **Workout log** (freeform entry + history)
3. **Progression charts** (body weight + per-exercise weight/volume over time)
4. **Chores** (promoted out of Tasks, first-class section here)
5. **Daily selfie** (capture + archive)
6. **Calorie counter** (light manual log)

---

## 1. Activity heatmap

A GitHub-commits-style grid, one box per day, but repurposed: instead of commit count driving
color intensity, **location/activity type drives the color, and duration or intensity drives the
shade**.

**Four activity types**, in priority order (highest wins when more than one happens on the same
day):

1. Goodlife with brother
2. Goodlife alone
3. Building workout room
4. Outside

Each has its own hue. If a day has only one activity, its color is unambiguous. If a day has more
than one (e.g. a morning run + evening gym session with your brother), the box takes the color of
the **highest-priority** activity, and a **small square in the corner** marks that a secondary
activity also happened that day (it doesn't need to say which one — hovering/clicking the day
shows the full list of sessions).

**Shading intensity**: open question, deferred. Log **both** duration (minutes) and a self-rated
intensity (originally a 1–10 RPE; now 1–5 stars — see above) on every session from day one, so
there's real data to decide with later whether duration, intensity, or a toggle between the two
drives the shade. Don't block building the
heatmap on this decision — default to duration since it's the more objective/frictionless of the
two to log, and revisit once a few weeks of data exist.

Clicking/hovering a day shows the session(s) logged that day (location, duration, intensity,
exercises).

## 2. Workout log

Entry stays **freeform text**, matching current habit exactly — e.g.:

```
bicep curls 20,10 20,10
squats 60,8 60,8 65,6
```

An LLM parses this into structured data on save, the same pattern already used for recipe
extraction (`backend/ai/recipes.py`) and journal metadata (`backend/ai/journal.py`): raw text goes
in, `{exercise, sets: [{weight, reps}, ...]}[]` comes out. The raw text is kept alongside the
parsed structure (mirroring `journal_entries.raw_content` vs `content`) so a bad parse never loses
data and can be re-run or manually fixed.

**Exercise name canonicalization** is the one real design problem here: "bicep curls," "bicep
curl," and "curls" all need to land on the same series for the progression chart in section 3 to
mean anything. Reuse the normalization approach already established in `backend/tags.py` (shared
normalization for tag-like strings) — maintain a canonical exercise list that grows as new names
are seen, and have the parser map onto the closest existing entry rather than creating near-duplicates
silently.

Each entry also records: date, location/activity type (the four categories above — this is what
feeds the heatmap), duration, self-rated intensity, and optional free-text notes.

**Mid-workout draft persistence (phone use is the primary case, not an edge case).** Logging
happens live, on a phone, mid-gym-session — and phone browsers reload the tab from scratch after
it's been backgrounded for a while (screen lock, switching to a music app between sets, a
notification), which silently wipes anything typed into a plain textarea. The fix is the same
category of problem the Writing chapter/note editors already solve with debounced autosave, just
persisted client-side instead of to the server: save the raw textarea content to `localStorage` on
every change (short debounce, ~300ms — no network round-trip, so it survives even if wifi/data
drops mid-set) and restore it on load. That means the textarea is never really "empty" after a
reload — whatever was last typed is still there to finish and submit. This should be treated as a
baseline requirement for the workout log, not a nice-to-have, since "paste in from another app" is
a workaround for a gap the UI itself should close.

Worth noting this is a mitigation, not a full fix: an installed **PWA** (see the "Mobile / tablet
access" item in `ROADMAP.md`) generally gets backgrounded/killed less aggressively by the mobile OS
than a plain browser tab, since it's treated more like a real app than a disposable page. That's a
reason to prioritize the PWA install angle when that roadmap item gets picked up — it reduces how
often the reload-and-lose-draft scenario happens at all — but it's complementary to, not a
substitute for, the localStorage draft save above, since even installed PWAs can still be killed
under memory pressure.

## 3. Progression charts

Two chart types:

- **Body weight over time** — simple line chart from manual weigh-in logs.
- **Per-exercise progression** — for a selected exercise (from the canonical list in section 2),
  chart weight (or volume = weight × reps, TBD which is more useful once there's data) over time.

Open question: no charting library exists in this codebase yet — will need to pick one (or hand-roll
something small in SVG/canvas, consistent with `src/lib/` pure-logic-first style) when this gets
built.

**Body weight logging**: manual, whenever you happen to weigh yourself — no reminder/nudge. Just a
quick "log today's weight" field on the Lifestyle tab.

## 4. Chores

Promoted out of Tasks into their own first-class section of the Lifestyle tab (not a separate
sidebar item). Likely reuses the shape of the existing daily-tasks pattern
(`daily_tasks` + `daily_task_completions`, reset per day) but as its own tables, since daily tasks
are capped at 4 and chores shouldn't share that limit or that list.

## 5. Daily selfie

**Capture**: settled as a hand-off to the device camera app (`<input type="file" accept="image/*"
capture="user">`) rather than an in-app `getUserMedia` widget — see "What the build settled".

**Storage**: one photo per day, stored next to the DB the same way fanfic/meeting media is
(`./data/lifestyle/selfies/<date>.jpg` or similar), not in SQLite as a blob.

**Timelapse video**: **archiving only for now.** The hard part is reliably not missing days, not
video generation — get that right first. An **on-demand "generate timelapse" export** (ffmpeg
stitching a date range into a video, same tool already used for meeting audio) is an explicit
later phase, not part of this build.

## 6. Calorie counter

Light manual log: a food description + calorie count per entry, grouped by day, with a daily
running total shown on the Lifestyle tab. No macro breakdown, no food database/API lookup for v1 —
just enough to eyeball daily totals. Structured similarly to the workout log (raw text entry,
lightly parsed) is a reasonable option if freeform entry ("chicken breast and rice, ~600") turns
out to be preferable to a form, but that's a build-time call, not a blocker for this doc.

---

## The tab absorbed Tasks (later change)

Reusing the todos rows was the right call and it kept going: the chores card was always a second
window onto rows the Tasks tab also showed, and the two views were checked at the same moments of
the day. So the Tasks tab was **removed** and its contents moved here, which is also why Lifestyle
now sits directly under Journal in the sidebar rather than down by Food.

- `src/components/Tasks/` survives as components, not a view — `TasksSection` mounts inside
  `Lifestyle.tsx` and keeps shortcut scopes 1 and 2 (nothing else in the tree registers one).
- The `chores` list is gone. `_merge_chores_into_todos` (`backend/db/connection.py`) folded the
  rows into `'todo'`, and `normalize_list` still accepts the old name so an offline mutation or a
  stale chat proposal replays instead of 400ing. `task_events.task_list` keeps its history.
- **DOM order is the phone's stacking order**, so the order below changed. It now runs by how
  often a thing is touched rather than by topic: **activity + momentum, tasks, workout log,
  calories | selfie, weight progression**. The workout log's history is capped at 4 sessions in a
  scroll box — beside the day's tasks, a fortnight of sessions pushed the page down.
- **Cards merged where two headers asked one question**: the heatmap and the momentum chart share
  a card, and so do daily tasks and the to-do list (daily above, capped at four, so it no longer
  claims half the tab for four rows). The shell lives once in `src/components/Lifestyle/card.ts`;
  the halves render no border of their own.
- **Cards cap their contents' width** rather than stretching a form across the tab, which is what
  lets the tasks card and the workout log share a row on the desktop and stack on the phone.
- **A fifth activity type, `lifting_home` ("Lifting at home")**, inserted between
  `building` and `outside` — index is priority, so it only changes which colour a _mixed_ day's
  box takes. Its hue (`#8a5bff`) came out of a search, not a picker: the four in use already sat
  ΔE 8.5 apart at their closest, 3:1 contrast against a dark surface caps how dark a candidate can
  be, and the categorical lightness band caps how light. The set of five passes every check on
  both surfaces (worst pair ΔE 8.1 deutan, 16.5 normal-vision, all ≥3:1). **A sixth will not
  fit** — the wheel is full at this lightness, and a sixth type needs a second channel.
- A **weekly trends chart** took the chores card's slot: applications sent against journal
  entries, `GET /api/lifestyle/trends`. One shared y-axis (both are counts per week; a second
  scale can be made to show any relationship you like), zero weeks included, hues reused from the
  validated activity palette.
- The tasks card got the workout log's **delete toggle** — 🗑 in the header reveals the per-row ✕,
  rather than every row carrying a live delete beside its own click targets. One toggle covers
  both lists in the card: two would read as one control that only half-works.

## Rough UI layout

Single scrollable column in the main content area (sidebar unchanged), top to bottom:

1. **Activity heatmap** — full width, own card at the top since it's the at-a-glance summary.
   Legend underneath (4 activity colors + secondary-activity mark); hovering/clicking a day shows
   that day's session(s).
2. **Workout log | Chores** — two cards side by side (stacked on narrow/Pocket 2 widths). Workout
   log: freeform textarea, location chip picker, duration field + intensity star picker,
   recent-entries list below. Chores: the Tasks view's own `TodoRow`/`TodoForm` — same rows, same
   chrome, and (the reason it's shared rather than copied) the same `isFarOffPeriodic` rule, so a
   monthly chore stays hidden until it's nearly due.
   _As built, after the change above: **Daily tasks | To-Do** comes second (still the Tasks
   components, still the same rows and the same `isFarOffPeriodic` rule), and the workout log
   pairs with the trends chart in the third row._
3. **Progression** — one card, two mini charts side by side: body weight sparkline (with an inline
   "log today's weight" field) and a per-exercise sparkline behind an exercise picker dropdown.
4. **Daily selfie | Calories** — two cards side by side. Selfie: capture widget + a short strip of
   recent-day thumbnails (so a missed day is visible at a glance). Calories: description + kcal
   input row, today's entries, running total.

A rough interactive sketch of this layout (Catppuccin-Mocha-matched to the app's existing palette
in `src/index.css`) was reviewed alongside this doc — not committed to source, since it's a
throwaway visualization rather part of the design record itself.

## Data model

The sketch below is what was proposed; `backend/db/schema.sql` is the source of truth. It landed
almost unchanged, with two differences: the `chores` / `chore_completions` tables were **not**
created (see above), and `workout_sessions` gained a `parse_status` column so the UI can show a
parse in flight and offer a retry on failure.

- `workout_sessions` — id, date, location_type (enum: outside/building/goodlife_alone/goodlife_brother),
  duration_minutes, intensity_rating (1–5 stars), raw_text, notes, parse_status
- `workout_exercises` — id, session_id, name_raw, name_canonical, position
- `workout_sets` — id, exercise_id, weight (NULL = bodyweight), reps, set_order
- `body_weight_logs` — id, date (unique — re-logging a day corrects it), weight
- ~~`chores`~~ / ~~`chore_completions`~~ — dropped; chores are `todos` rows with `list='chores'`
- `lifestyle_selfies` — id, date (unique), path, mime
- `calorie_logs` — id, date, description, calories

## Open questions carried forward

Still open (both are now toggles in the UI, waiting on real data rather than on a decision):

- Duration vs. intensity for heatmap shading — currently a toggle defaulting to duration.
- Weight vs. volume for the per-exercise progression chart — currently a toggle.

Settled during the build (see "What the build settled" above): charting library (none —
hand-rolled SVG), canonicalization strategy (one-directional token folding, then fuzzy at
ratio ≥ 0.87, plus a merge endpoint as the manual escape hatch), calorie entry (local regex
parse, no LLM), and Pocket 2 camera viability (moot — the file-input fallback always works).

## Build order (1–6 done; 7 not started)

1. ~~Workout log (freeform entry + AI parse + canonical exercise list)~~ — the foundation, as
   expected: it's what makes both the heatmap and the progression chart possible.
2. ~~Activity heatmap.~~
3. ~~Body weight log + progression charts.~~
4. ~~Chores section~~ — smaller than planned once it became clear the rows already existed.
5. ~~Daily selfie capture + archive.~~
6. ~~Calorie counter.~~
7. _(Later phase)_ Timelapse video export — **not built**, as the doc intended.
