# Lifestyle tab — design doc

**Status: design only, no implementation yet.** This captures the plan discussed for a new
top-level "Lifestyle" view covering workout tracking, a GitHub-commits-style activity heatmap,
daily selfies, chores, body weight, and (lightly) calorie tracking.

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
intensity (e.g. 1–10 RPE) on every session from day one, so there's real data to decide with later
whether duration, RPE, or a toggle between the two drives the shade. Don't block building the
heatmap on this decision — default to duration since it's the more objective/frictionless of the
two to log, and revisit once a few weeks of data exist.

Clicking/hovering a day shows the session(s) logged that day (location, duration, RPE, exercises).

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

**Capture**: in-app camera widget (browser `getUserMedia`) right in the Lifestyle tab — snap and
it uploads directly. Needs a check on the Pocket 2 for whether it has a usable camera and how
`getUserMedia` behaves there; desktop should just work.

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

## Rough UI layout

Single scrollable column in the main content area (sidebar unchanged), top to bottom:

1. **Activity heatmap** — full width, own card at the top since it's the at-a-glance summary.
   Legend underneath (4 activity colors + secondary-activity mark); hovering/clicking a day shows
   that day's session(s).
2. **Workout log | Chores** — two cards side by side (stacked on narrow/Pocket 2 widths). Workout
   log: freeform textarea, location chip picker, duration/RPE fields, recent-entries list below.
   Chores: plain checklist, "+ Add chore" affordance.
3. **Progression** — one card, two mini charts side by side: body weight sparkline (with an inline
   "log today's weight" field) and a per-exercise sparkline behind an exercise picker dropdown.
4. **Daily selfie | Calories** — two cards side by side. Selfie: capture widget + a short strip of
   recent-day thumbnails (so a missed day is visible at a glance). Calories: description + kcal
   input row, today's entries, running total.

A rough interactive sketch of this layout (Catppuccin-Mocha-matched to the app's existing palette
in `src/index.css`) was reviewed alongside this doc — not committed to source, since it's a
throwaway visualization rather part of the design record itself.

## Suggested data model sketch

Not final — for orientation when this gets built.

- `workout_sessions` — id, date, location_type (enum: outside/building/goodlife_alone/goodlife_brother),
  duration_minutes, intensity_rating, raw_text, notes
- `workout_exercises` — id, session_id, exercise_name_raw, exercise_name_canonical
- `workout_sets` — id, exercise_id, weight, reps, set_order
- `body_weight_logs` — id, date, weight
- `chores` — id, name, archived, created_at (mirrors `daily_tasks` shape)
- `chore_completions` — chore_id, date (mirrors `daily_task_completions`)
- `lifestyle_selfies` — id, date, file_path
- `calorie_logs` — id, date, description, calories

## Open questions carried forward

- Duration vs. RPE (or a toggle) for heatmap shading — decide once real data exists.
- Weight vs. volume (weight × reps) for the per-exercise progression chart.
- Charting library choice.
- Exercise-name canonicalization strategy in detail (fuzzy match threshold, manual merge UI for
  mis-canonicalized entries).
- Whether calorie entries should be freeform-AI-parsed like workouts, or a plain form.
- Pocket 2 camera viability for the selfie capture widget.

## Suggested build order (rough, not committed)

1. Workout log (freeform entry + AI parse + canonical exercise list) — this unlocks both the
   heatmap and the progression chart, so it's the foundation.
2. Activity heatmap.
3. Body weight log + progression charts.
4. Chores section (straightforward, low risk, high daily value).
5. Daily selfie capture + archive.
6. Calorie counter.
7. _(Later phase)_ Timelapse video export.
