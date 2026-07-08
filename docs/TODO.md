# TODO — next three updates

The first three items to build, picked from [ROADMAP.md](./ROADMAP.md) as the best mix of *easiest* and *most impactful* right now. All three are self-contained (no OAuth, no external APIs, no new infrastructure), fully coverable by automated tests, and pay off daily.

## 1. Local encrypted backups

**Why first:** the roadmap calls it mandatory, and the risk grows with every entry written. The local half is genuinely easy — the hard parts (Dropbox/Google upload, OAuth) can wait for phase 2 without reducing the protection much.

- Snapshot `data/lunaschal.db` (via SQLite's backup API, safe under WAL) + media files into a zip.
- Encrypt with AES-256-GCM using a one-time generated key at `~/.config/lunaschal/backup.key` (chmod 600); show the key once for the password manager (scheme in ROADMAP.md).
- Trigger: on app startup once per day, plus a manual "Back up now" button in Settings.
- Retention: keep last 7 daily + 4 weekly snapshots in a configurable backup directory (ideally another disk).
- Tests: round-trip a backup → restore → verify DB contents; retention pruning; key-file permissions.
- *Phase 2 (later):* encrypted upload to Dropbox/Google Drive.

## 2. Todos: due dates, priorities, overdue view

**Why:** the todo section is bare CRUD (`backend/routes/tasks.py`) and it's used every day — small schema additions produce a big usability jump. Also groundwork for email routing and voice commands creating "real" todos later.

- Add `due_date` and `priority` columns (ALTER TABLE migration in `backend/db/connection.py`, matching the existing `writing_project_id` pattern).
- API: filter/sort by due date and priority; an "overdue" flag computed server-side.
- Frontend: grouping (Overdue / Today / Upcoming / Someday), priority indicator, overdue highlighting.
- Teach the voice-command parser (`backend/ai/commands.py`) to extract due dates ("remind me Friday").
- Tests: route filters/sorting, overdue computation around date boundaries, parser date extraction.
- *Phase 2 (later):* recurring todos, subtasks.

## 3. Habit tracking with streaks

**Why:** highest delight-per-line-of-code on the roadmap — one new table, simple queries, a small grid UI — and it makes opening the app every day the default, which feeds the journal and todos too.

- New tables: `habits` (name, created, archived) and `habit_checks` (habit_id, date, unique together).
- API: CRUD for habits, toggle a check for a date, and a stats endpoint (current streak, longest streak, completion % over last 30 days).
- Frontend: habit grid in the journal day view — one row per habit, tap to check off; streak counter per habit.
- Streak logic is pure date math → perfect for exhaustive unit tests (gaps, today-unchecked, timezone/day boundaries).

---

**Next up after these** (in rough order): journal "on this day last year" + view modes, global search across content types, weekly AI review.
