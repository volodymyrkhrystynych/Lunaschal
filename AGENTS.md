# AGENTS.md

This file provides instructions to Codex when working in this repository.

## Instruction sources

The detailed project documentation currently lives in `CLAUDE.md` files. Treat
them as repository instructions, not as Claude-only documentation:

1. Before doing repository work, read the root `CLAUDE.md` completely.
2. When working in a directory that contains a `CLAUDE.md`, read that file too.
3. Follow the most specific applicable file when instructions conflict. Direct
   user instructions and higher-priority Codex instructions still take
   precedence.
4. Do not mechanically rename or duplicate the nested files. They are the
   canonical architecture and feature notes until they are deliberately
   migrated.

Applicable nested guides currently include:

- `backend/delegate/CLAUDE.md`
- `backend/fanfic/CLAUDE.md`
- `backend/jobs/CLAUDE.md`
- `backend/journal/CLAUDE.md`
- `backend/learning/CLAUDE.md`
- `backend/lifestyle/CLAUDE.md`
- `backend/meetings/CLAUDE.md`
- `backend/paper/CLAUDE.md`
- `backend/practice/CLAUDE.md`
- `backend/research/CLAUDE.md`
- `src/components/Chat/CLAUDE.md`
- `src/components/Writing/CLAUDE.md`
- `stt/CLAUDE.md`

References inside those documents to “Claude” mean the active coding agent and
therefore apply to Codex as well, except where this file supplies a
Codex-specific equivalent.

## Codebase exploration

Prefer the installed `graphify` CLI for architectural or cross-module questions;
it understands dependencies and data flow across this interconnected codebase.
Use repository search (`rg`) for exact strings, filenames, and small local
lookups.

```bash
graphify query "<question>"
graphify update
```

The global Claude `/graphify` slash command maps to these CLI commands in Codex.
There is no need to switch to a Claude model before updating the graph. If
`graphify` is unavailable or its graph is stale or incomplete, fall back to
`rg`, file inspection, and tests, and say so when it affects confidence.

## Working conventions

Development happens on both a desktop and a low-powered, keyboard-first GPD
Pocket 2. Favor automated verification over asking the user to perform manual UI
click-through testing.

### Prefer the `codex` worktree

- Prefer doing Codex work in the git worktree whose directory is named `codex`,
  rather than in the user's primary checkout. This is the Codex-specific
  equivalent of references to the `claude` worktree in `CLAUDE.md`.
- If the `codex` worktree does not exist, create it when the approved branch
  workflow permits doing so.
- Whenever moving into the `codex` worktree, bring its branch up to date with
  `main` before making changes. Fetch first when a remote is available, then
  rebase or merge the current `main` into the worktree branch as appropriate.
- Inspect the worktree before synchronizing it. If it contains uncommitted work,
  preserve it and ask the user before rebasing, resetting, or otherwise changing
  that work.

### Branch per feature

- For a new feature, do not implement directly on `main`.
- Before creating a feature branch, ask the user for permission and propose a
  branch name during planning. Let the user confirm or replace it.
- Use the existing naming style: `feat/<short-kebab-description>` for features
  and `fix/<short-kebab-description>` for fixes.
- If already on a relevant feature branch, continue there. Otherwise branch from
  an up-to-date `main` after approval.
- Commit or push only when the user asks.

### Tests are the primary safety net

- Add automated tests for new behavior and bug fixes when practical.
- Run the relevant tests after changes and report actual results. A relevant
  green suite is the default completion bar.
- Prefer fast isolated tests. Mock external AI providers and network calls.
- Backend tests use pytest and an automatically isolated SQLite database.
- Frontend tests use Vitest. Pure logic belongs in `src/lib/`; `.test.tsx`
  component tests opt into jsdom with `// @vitest-environment jsdom`.
- Do not defeat `pytest.ini`'s temporary-path retention or the batching in
  `scripts/test-backend.sh`; both prevent full runs from exhausting temporary
  storage.
- There is no ESLint. Prettier is the formatter.

Useful commands:

```bash
npm run dev
npm run dev:flask
npm run dev:client
npm run dev:desktop
python main.py
python main.py --headless

npm run test:backend
.venv/bin/pytest backend/tests/test_foo.py
npm run test
npm run test:all
npm run test:watch
npm run format
npm run format:check
```

## High-value project invariants

The root and nested `CLAUDE.md` files contain the full architecture and feature
details. Keep these especially important constraints in mind:

- Lunaschal is a single-user React/TypeScript + Flask/SQLite desktop/LAN app.
- Local AI goes through llama.cpp's OpenAI-compatible server and the shared
  `backend/ai/` layer. Do not add a parallel provider path casually.
- There is one reusable tool loop in `backend/research/agent.py`; new agents
  supply tools and dispatch rather than copying the loop.
- `backend/db/schema.sql` is the schema source of truth. New SQLite migrations
  follow the idempotent `_ensure_*` pattern in `backend/db/connection.py`.
- Database IDs are ULIDs. Database timestamps are Unix integers and are converted
  and camel-cased by `row_to_dict`.
- Use shared normalization and validation helpers such as `backend/tags.py` and
  `backend/geo.py`; do not grow feature-specific alternatives.
- Adding a top-level frontend view requires coordinated edits to `VIEWS` in
  `src/lib/viewPersistence.ts`, the switch in `src/App.tsx`, `View` and
  `navItems` in `src/components/Sidebar.tsx`, and `AppView` and `VIEW_ORDER` in
  `src/shortcuts/ShortcutProvider.tsx`.
- Shortcut scope numbers must be unique within a mounted tree. The number row is
  reserved for Learning review ratings.
- Long-running in-memory work that must survive restart needs a database
  checkpoint and startup recovery.
- Tests set `LUNASCHAL_NO_SCHEDULERS`; preserve that boundary for daemon loops.
- Production Flask uses port 5000, development Flask uses 5001, and Vite uses 5173. Development launchers must not kill or bind the production port.
- Keep `data/**` ignored by Vite's watcher because SQLite WAL churn can exhaust
  memory.
- The browser extension is loaded unpacked and has no build step. Its source
  files are the deployed artifact.

## Working style for Codex

- Inspect the current worktree before editing and preserve unrelated user
  changes.
- Use `rg`/`rg --files` for exact repository searches and `apply_patch` for
  hand-authored edits.
- Read nearby tests and the applicable nested guide before changing a feature.
- Reuse existing helpers and patterns before adding abstractions.
- Keep changes scoped to the request. Do not commit, push, install services, or
  mutate production data unless explicitly asked.
- For potentially destructive or externally visible actions, verify the exact
  target and obtain any required approval first.
- In the handoff, lead with the result, list changed files, give test commands and
  outcomes, and identify any remaining verification honestly.
