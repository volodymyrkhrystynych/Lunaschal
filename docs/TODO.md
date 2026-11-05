# TODO — next three updates

The first three items to build, picked from [ROADMAP.md](./ROADMAP.md) as the best mix of _easiest_ and _most impactful_ right now. All three are self-contained (no OAuth, no external APIs, no new infrastructure), fully coverable by automated tests, and pay off daily.

**Status audited against `main` on 2026-10-27:** #1 is half done, #2 is done apart from the frontend grouping, #3 has not started.

## 1. Local encrypted backups — 🟡 backup built, encryption not

**Why first:** the roadmap calls it mandatory, and the risk grows with every entry written.

Built differently from this plan: a nightly `ops/backup.sh` rsyncs the DB and media to another disk, with the destination and retention configured in Settings → Backup and the panel judging health by the newest snapshot's age. What's left:

- Encrypt with AES-256-GCM using a one-time generated key at `~/.config/lunaschal/backup.key` (chmod 600); show the key once for the password manager (scheme in ROADMAP.md).
- Tests: round-trip a backup → restore → verify DB contents; key-file permissions.
- _Phase 2 (later):_ encrypted upload to Dropbox/Google Drive.

## 2. Todos: due dates, priorities, overdue view — ✅ mostly done

`due`, `priority` and recurrence (`repeat_interval`/`repeat_unit`) are columns on `todos`, overdue is computed and highlighted (`src/lib/todos.ts`), and the date rules live in `backend/todo_recurrence.py`. Remaining:

- Frontend grouping (Overdue / Today / Upcoming / Someday).
- _Phase 2 (later):_ subtasks.

## 3. Habit tracking with streaks — ⬜ not started

**Why:** highest delight-per-line-of-code on the roadmap — one new table, simple queries, a small grid UI — and it makes opening the app every day the default, which feeds the journal and todos too.

- New tables: `habits` (name, created, archived) and `habit_checks` (habit_id, date, unique together). Seed both in `scripts/seed_test_db.py`.
- API: CRUD for habits, toggle a check for a date, and a stats endpoint (current streak, longest streak, completion % over last 30 days).
- Frontend: habit grid — one row per habit, tap to check off; streak counter per habit. The Lifestyle view, beside daily tasks, is the natural home now that the Journal is a feed.
- Streak logic is pure date math → perfect for exhaustive unit tests (gaps, today-unchecked, the 4am day boundary in `backend/day_boundary.py`).

---

**Next up after these** (in rough order, none started): journal "on this day last year" + view modes, global search across content types, weekly AI review.

## Parked

- **Getting an iOS voice memo into a journal entry.** Largely sidestepped: the native app (`apple/`) records straight into the journal, so the Voice Memos round-trip is no longer the only way to capture audio on the phone. Importing an _existing_ Voice Memos recording still doesn't work — the share extension accepts URLs, text and images, not audio. Dead ends are in [learnings/ios-voice-memo-capture.md](./learnings/ios-voice-memo-capture.md); adding audio to the share extension's activation rule is now the obvious next thing to try.
