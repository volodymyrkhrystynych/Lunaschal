# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working conventions

Development happens on two machines: a desktop (comfortable, full mouse/keyboard) and a GPD Pocket 2 — a low-powered handheld with no usable mouse. On the Pocket 2, manual click-through testing is slow and painful, so the workflow leans on branches and automated tests so changes can be verified without a hands-on walkthrough.

### Branch per feature
- **Whenever the user asks for a new feature, start it on a fresh branch** — don't build features on `main`. Create the branch before writing code.
- Branch naming follows the existing convention: `feat/<short-kebab-description>` for features, `fix/<...>` for bug fixes. Match the style of recent branches (e.g. `feat/voice-command-shortcut`).
- If the user asks for a feature while already on a relevant feature branch, keep working there; otherwise branch off the up-to-date `main`.
- Only commit or push when the user asks.

### Tests are the primary safety net
- Because manual testing is impractical on the Pocket 2, **new features and bug fixes should come with automated tests** that exercise the behavior. Prefer proving a change works with a test over asking the user to click through the UI.
- There is currently no test runner configured. When adding the first tests, set one up: **pytest** for the Flask/Python backend (`backend/`), and **Vitest** for the React/TypeScript frontend (`src/`). Add matching `npm run test` / `npm run test:backend` scripts so tests are runnable with one command.
- Favor fast, isolated tests: unit-test the AI parsing/classification logic (`backend/ai/`), route handlers, the SM-2 flashcard algorithm, and DB layer against a temporary SQLite file. Mock external AI providers rather than calling them.
- After making changes, run the relevant tests and report the actual results. Treat a green test suite — not a manual walkthrough — as the default bar for "done."

### Show a change map before implementing
- **Before writing code for anything that touches more than one file**, present a short plan as a Mermaid diagram (e.g. `graph TD`) showing which files/modules will be changed and how they connect, plus a one-line note per node on what changes there. Wait for a go-ahead before implementing.
- The terminal can't render Mermaid — use the `mermaid` MCP server's `mermaid_preview` tool to render the diagram (opens in browser with live reload) instead of pasting raw Mermaid source into the chat.
- Skip the diagram for trivial single-file tweaks (typo fixes, one-line bug fixes, config value changes) — use judgment on "trivial."

## STT (Speech-to-Text)

STT/TTS is embedded directly in the Flask backend (`backend/routes/stt.py`). Two backends — local (openai-whisper + kokoro-onnx) or OpenAI API (cloud, no local models).

```bash
# --- Local setup (GPU machine) ---
bash stt/setup.sh           # installs openai-whisper, kokoro-onnx, openwakeword

# --- API setup (low-power machine) ---
bash stt/setup.sh --api     # installs only openai client, skips local models
export OPENAI_API_KEY=sk-...
export STT_BACKEND=openai
export TTS_BACKEND=openai

# Terminal 1 — Flask app (handles STT/TTS routes)
npm run dev

# Terminal 2 — global voice input listener (keyboard shortcuts + audio capture)
./stt/run_listener.sh       # or: npm run stt

# Terminal 3 (optional) — morning check-in daemon
./stt/run_morning_checkin.sh

# Test morning check-in immediately (skips wake detection)
./stt/run_morning_checkin.sh --now
```

Shortcuts:
- **F1** (`STT_PASTE_KEY`) — record → transcribe → paste text at cursor via `wtype`
- **Right Alt** (`STT_VOICE_KEY`) — record → transcribe → AI chat (Lunaschal `/api/chat/stream`) → TTS reply spoken aloud
- (`STT_JOURNAL_KEY`) — record → transcribe → save as journal entry
- (`STT_COMMAND_KEY`) — record → transcribe → LLM parses the command (`POST /api/voice-command`, parser in `backend/ai/commands.py`) and creates a todo, calendar event, or journal entry; if essential details are missing the LLM asks a clarifying question via TTS and the listener records the answer (max 3 rounds)

All four shortcuts are rebindable in Settings → Voice Shortcuts (stored in the `settings` table; env vars are fallbacks).

The Flask backend handles `POST /api/transcribe` and `POST /api/tts` directly (no separate port 8765 service). The Whisper model loads lazily on the first transcription request. `stt/service.py` still exists as a standalone FastAPI server but is no longer used by default.

**Local TTS**: Kokoro-ONNX (~80 MB model cached to `~/.cache/lunaschal/tts/` on first run). **API TTS**: OpenAI (`tts-1`, voice configurable via `OPENAI_TTS_VOICE`, default `nova`). Voice assistant conversation history is kept in-memory for the lifetime of the listener process. `LUNASCHAL_URL` env var overrides the chat server URL (default: `http://127.0.0.1:5000`).

STT/TTS env vars summary:

| Var | Default | Notes |
|-----|---------|-------|
| `STT_BACKEND` | `local` | `local` or `openai` |
| `TTS_BACKEND` | `local` | `local` or `openai` |
| `OPENAI_API_KEY` | — | Required for openai backends |
| `OPENAI_TTS_VOICE` | `nova` | alloy / echo / fable / onyx / nova / shimmer |
| `WHISPER_MODEL` | `turbo` | Local STT only (tiny/base/small/medium/large/large-v2/large-v3/turbo) |
| `WHISPER_DEVICE` | `cuda` | Local STT only (`cuda` or `cpu`) |
| `STT_LISTENER` | — | Set to `1` to auto-start the voice listener as a subprocess of Flask |

### Morning Check-in (`stt/morning_checkin.py`)

Daemon that monitors for wake-from-sleep events via a time-jump trick (sleeps 10 s; if the wall clock advanced >30 s, the system was suspended). When the machine wakes between `MORNING_START_HOUR` (default 8) and `MORNING_END_HOUR` (default 11), it starts a voice conversation that helps the user rubber-duck their plans for the day. A flag file in `$XDG_RUNTIME_DIR` prevents duplicate check-ins within the same calendar day.

Env vars: `STT_URL`, `LUNASCHAL_URL`, `MORNING_START_HOUR`, `MORNING_END_HOUR`.

## Commands

```bash
# Development (Flask backend on :5000 + Vite client on :5173)
npm run dev

# Run only the backend
npm run dev:flask        # flask --app backend.app run --port 5000 --debug

# Run only the frontend
npm run dev:client       # vite

# Open as a desktop window (PyWebView loads the Vite dev server)
python main.py --dev

# Production build + open desktop window
npm run build
python main.py

# Run the voice input listener (Flask app must already be running)
npm run stt

# Tests
npm run test:backend     # pytest (backend/tests) — needs: .venv/bin/pip install -r requirements-dev.txt
npm run test             # vitest run (frontend, src/**/*.test.ts)
npm run test:all         # both suites
npm run test:watch       # vitest in watch mode
```

Tests: **pytest** for the backend (`backend/tests/`, config in `pytest.ini`) and **Vitest** for the frontend (config in the `test` block of `vite.config.ts`). Vitest currently runs in the `node` environment; add `jsdom` + `@testing-library/react` before writing component tests. No linting is configured.

## Architecture

Lunaschal is a single-user personal knowledge management desktop app with AI integration. It combines a journal, calendar, flashcard system (spaced repetition), creative writing workspace, file editor, and AI chat with RAG. Runs as a native desktop window via PyWebView, or as a web app on the LAN in network mode.

### Stack
- **Desktop shell**: PyWebView — `main.py` starts Flask in a background thread then opens a `webview.create_window`
- **Frontend**: React 19 + Vite + Tailwind CSS v4 — in `src/`
- **Backend**: Flask (Python) — in `backend/`
- **API layer**: REST JSON + React Query; typed client in `src/hooks/api.ts`
- **Database**: SQLite via Python's built-in `sqlite3`; stored at `./data/lunaschal.db`
- **AI**: `openai`, `google-generativeai`, and `ollama` Python SDKs

### Entry Points

- **`main.py`** — PyWebView desktop launcher. Starts Flask in a daemon thread, waits for `/api/health`, then opens the window. Pass `--dev` to point the window at the Vite dev server instead of the built `dist/`.
- **`backend/app.py`** — Flask app factory (`create_app`). Runs DB init, registers all blueprints, mounts auth middleware, and serves the built `dist/` as static files in production.

### Backend Structure (`backend/`)

Flask blueprints in `backend/routes/`: `auth`, `journal`, `calendar`, `flashcard`, `settings`, `rag`, `chat`, `files`, `writing`, `curated_tags`.

The chat blueprint exposes a streaming SSE endpoint at `POST /api/chat/stream` using Flask's `Response(stream_with_context(...))`.

### Database Layer (`backend/db/`)
- `schema.sql` — raw SQL `CREATE TABLE IF NOT EXISTS` statements; all IDs are ULIDs
- `connection.py` — opens a single WAL-mode SQLite connection (`get_db()`), runs `schema.sql` on startup, initializes FTS5 triggers and the sqlite-vec virtual table, ensures the network code exists, and safely adds `writing_project_id` to `conversations` via ALTER TABLE migration
- FTS5 virtual table (`journal_fts`) is maintained by SQL triggers defined in `connection.py`
- `sqlite-vec` extension for vector similarity search (RAG); silently skipped if not installed
- `curated_tags` table stores user-defined tag names (unique); `journal_entry_curated_tags` is the many-to-many join table — both cascade-delete on parent removal

### AI Layer (`backend/ai/`)
- `provider.py` — resolves the active AI provider and model from DB settings (or env vars `OPENAI_API_KEY`, `GOOGLE_API_KEY`); supports `openai`, `gemini`, `ollama`
- `classifier.py` — classifies chat messages into intents: `journal | calendar | question | flashcard_request | conversation`; extracts structured data when saving entries
- `embeddings.py` — generates text embeddings for RAG; OpenAI (`text-embedding-3-small`) and Gemini (`text-embedding-004`) supported; Ollama embeddings not yet implemented
- `rag.py` — syncs journal entries to embeddings, performs semantic search, formats retrieved context for the LLM
- `flashcards.py` — AI-assisted flashcard generation
- `chat.py` — streaming chat generator consumed by the `/api/chat/stream` route
- `journal.py` — also contains `classify_entry_for_tag(content, tag_name) -> bool`: binary LLM classifier used by the curated-tag background scan; runs on CPU via `_CPU_OPTIONS` for Ollama

### Auth (`backend/auth.py`)
Single-user auth via JWT cookie (`lunaschal_token`, 30-day expiry). **Auth is only enforced in network mode** (`NETWORK_MODE=1`) and only for non-localhost requests — the `check_auth` middleware in `app.py` returns early when `is_localhost(request)` is true. Network mode login requires both the password and a rotating 6-digit display code (pseudo-2FA); the code is stored in the `settings` table and can be regenerated from the Settings page.

### Frontend Structure (`src/`)
- `App.tsx` — top-level view router; checks auth status on load, shows `Login` if unauthenticated in network mode, otherwise shows a sidebar + main view (chat/journal/writing/calendar/flashcards/files/settings)
- `src/components/` — one file per view/feature; `Editor/` subdirectory for the file editor and STT panel; `Writing/` subdirectory for the writing workspace; `CuratedTagsSection.tsx` is the Settings > Tags tab component
- `src/hooks/api.ts` — typed REST client (`api.*` namespaces) using plain `fetch`; no tRPC
- `@` path alias resolves to `./src/`
- CSS custom properties (e.g. `var(--color-bg)`) are used for theming throughout

### Writing Module (`src/components/Writing/`, `backend/routes/writing.py`)
Two-panel layout: left nav (project list + a `WritingNav` with Chapters/Notes/Discussions sections) | full-width center panel that switches on the selected item: chapter → prose editor, note → note editor, discussion → chat view. `WritingNav` owns the single shortcut scope 2 for the whole nav (flattened chapters→notes→discussions stepping) — never add a second `useShortcutScope(2, …)` in the Writing tree; the last registration silently wins.

**DB tables**: `writing_projects`, `writing_chapters` (ordered by `position`), `writing_context_docs` (typed: `character | outline | worldbuilding | note`). "Notes" in the UI/API are stored in `writing_context_docs` (HTTP paths are `/api/writing/.../notes`; the table name is legacy). Discussions reuse the existing `conversations` + `messages` tables; `conversations.writing_project_id` scopes them to a project, and the general Chat tab filters them out (`writing_project_id IS NULL`). Deleting a project deletes its discussions.

**Chapter/note editors**: plain `<textarea>` (not CodeMirror — prose, not code) with 1.5 s debounced auto-save; chapters add live word count and font-size shortcuts.

**Discussions**: full-size chat reusing `/api/chat/stream` unchanged. The frontend assembles a `systemPrompt` from the project title/description and any notes the user has checked in the context panel. No journal/calendar classification — pure story chat. A **Summarize** button (`POST /api/writing/conversations/<id>/summarize`) distills the transcript via `backend/ai/writing.py:summarize_discussion` into a new note (`doc_type='note'`).

### Key Behaviors
- **Curated tags** — user-defined tags managed in Settings → Tags tab. Each new tag triggers a background daemon thread that calls `classify_entry_for_tag` per journal entry and writes matches to `journal_entry_curated_tags`. Progress tracked in-memory (`_scan_progress` dict in `curated_tags.py`); the list endpoint merges it in. Tags appear as filter pill buttons in the Journal view; entries display curated tags (`#name`, neutral style) separately from freeform AI tags (accent color).
- **Flashcards** use the SM-2 spaced repetition algorithm (implemented in `backend/routes/flashcard.py`)
- **RAG** is optional — silently disabled when embeddings aren't configured (Ollama provider, or missing API key)
- **DB path** defaults to `./data/lunaschal.db`; override with `DATABASE_URL` env var
- **JWT secret** defaults to a hardcoded dev string; set `JWT_SECRET` env var in production
- **Flask port** is always 5000; Vite dev server is 5173 and proxies `/api` to Flask
- **Network mode**: set `NETWORK_MODE=1` and `LUNASCHAL_PASSWORD=...` to bind `0.0.0.0` and enforce auth for LAN access
