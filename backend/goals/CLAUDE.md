# Goals (`backend/goals/`)

One **overarching goal** — a block of text, what everything else is in service of — and any number of goals for **this week, month, quarter and year**. They live on the Lifestyle tab above the habits (`src/components/Lifestyle/GoalsCard.tsx`) and on the phone's Todo tab (`apple/App/TodoViews.swift`), and they are read into every chat prompt and the morning briefing through `service.context_text()`. This replaced `settings.briefing_goals`, a free-text box in Settings → Briefing: `_ensure_goal_horizons` copied that text into the overarching goal once and cleared the column.

## The journal is the history

There is no revisions table. **Every change a person makes to a goal — create, rename, re-describe, end, drop, redeem, replace the overarching goal — leaves a journal entry**, written the way a dictated entry is (`raw_content` set, polished in the background) and linked by `journal_entries.goal_id`. `GET /api/goals/<id>/history` is just those entries. Three consequences:

- **A create uses the goal's own id as the entry id**, so an offline replay from the phone cannot write a second entry; an edit that changes nothing writes nothing.
- **The overarching goal is never edited in place.** Changing it ends the old row and inserts a new one, so each version keeps its own dates and its own entry ("New overarching goal: … It replaces: …"). A partial unique index keeps one open at a time.
- **A goal's entries are never evidence for it.** `_sources` excludes `goal_id IS NOT NULL`; otherwise "New goal: do pushups" would be offered to the judge as proof of pushups. That is why `create_journal_entry` takes `goal_id` in the INSERT rather than having it set afterwards.

## Periods

`periods.py` is pure: weeks run Monday–Sunday, quarters start in Jan/Apr/Jul/Oct, and every argument is a **4am day key**, never a wall-clock date. A goal stores its `period_start`/`period_end`; `current()` lists the open goals per horizon, flags one whose period has passed but which nobody has closed (`overdue`), and marks a horizon `unset` when nothing open covers today — the prompt to set one.

## Daily rules and rewards are optional

A period goal may carry the original Goals feature: a daily criterion, a streak length and a prize, judged per 4am day from workouts and journal entries (`evidence.judge`, cached by a fingerprint of the sources). Most goals have none and are never evaluated. Where a rule exists it is **immutable** — `edit()` refuses to change criteria, reward, start or length, because changing what counts after the fact is how a reward gets gamed. A rule goal started late in its period still gets its whole streak: `goal_period` stretches `period_end` to cover `target_days`. Rules are set up in chat (`create_goal` with `criteria`/`reward`), not in the phone's editor, so they can be talked through first.

Ending a goal keeps its reward state (`earned`/`redeemed`/`needs_review` survive; only `active` becomes `ended`) and stops evaluation at the day it ended.

## Migration

Pre-horizon `goals` had NOT NULL criteria/reward/target_days, which SQLite cannot relax, so `_ensure_goal_horizons` rebuilds the table from the statement in `schema.sql` with foreign keys off (keeping `goal_days`, the verdicts behind every reward) and files each old goal under the horizon its length fits.
