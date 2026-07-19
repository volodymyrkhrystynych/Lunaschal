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
- Both suites are configured and extensive: **pytest** for the backend (`backend/tests/`, config in `pytest.ini`; run `.venv/bin/pip install -r requirements-dev.txt` once) and **Vitest** for the frontend (config in the `test` block of `vite.config.ts`).
- Vitest defaults to the `node` environment for pure-logic tests; component tests (`.test.tsx`) opt into jsdom per-file with a `// @vitest-environment jsdom` pragma and use `@testing-library/react` (auto-cleanup is registered in `src/test/setup.ts`). Extractable logic lives in `src/lib/` precisely so it can be tested without jsdom.
- Favor fast, isolated tests: unit-test AI parsing/classification (`backend/ai/`), route handlers, the FSRS scheduling adapter, pure parsers (`backend/fanfic/xenforo.py`, `backend/meetings/merge.py`), and the DB layer against a temporary SQLite file. Mock external AI providers and network fetches rather than calling them.
- After making changes, run the relevant tests and report the actual results. Treat a green test suite — not a manual walkthrough — as the default bar for "done."
- No ESLint; **Prettier** runs on staged files via a pre-commit hook (`simple-git-hooks` + `lint-staged`). `npm run format` / `format:check` run it manually.

## Commands

```bash
# Development (Flask backend on :5000 + Vite client on :5173)
npm run dev

npm run dev:flask        # backend only (flask --app backend.app run --port 5000 --debug)
npm run dev:client       # frontend only (vite)
npm run dev:desktop      # desktop window via PyWebView pointed at the Vite dev server
python main.py           # production: build first with npm run build, then desktop window

# Convenience launchers
./start.sh               # kills stale :5000/:5173, starts ollama if needed, npm run dev
./start-server.sh        # network mode (NETWORK_MODE=1, requires LUNASCHAL_PASSWORD)
./start-node.sh          # frontend-only on a weak machine; proxies /api to a remote
                         # backend via VITE_API_PROXY_TARGET/LUNASCHAL_URL (Tailscale)

# Voice input listener (Flask app must already be running)
npm run stt              # or ./stt-start.sh

# Tests
npm run test:backend     # pytest (backend/tests)
npm run test             # vitest run (src/**/*.{test,spec}.{ts,tsx})
npm run test:all         # both suites
npm run test:watch       # vitest in watch mode
```

## Architecture

Lunaschal is a single-user personal life-management desktop app with AI integration. Views (in sidebar order): AI chat with RAG, daily tasks + todos, journal, meeting recorder/transcriber, creative-writing workspace, calendar, spaced-repetition learning, cookbook, fanfic library/reader, newspaper front pages, file editor, settings. Runs as a native desktop window via PyWebView, or as a web app on the LAN in network mode.

### Stack

- **Desktop shell**: PyWebView — `main.py` starts Flask in a background thread then opens a `webview.create_window`
- **Frontend**: React 19 + Vite + Tailwind CSS v4 — in `src/`
- **Backend**: Flask (Python) — in `backend/`
- **API layer**: REST JSON + React Query; typed client in `src/hooks/api.ts` (one `api.*` namespace per feature)
- **Database**: SQLite via Python's built-in `sqlite3`; stored at `./data/lunaschal.db`
- **AI**: `openai`, `google-generativeai`, and `ollama` Python SDKs
- `drizzle.config.ts` is vestigial (points at a `server/db/schema.ts` that no longer exists) — the schema source of truth is `backend/db/schema.sql`

### Entry Points

- **`main.py`** — PyWebView desktop launcher. Starts Flask in a daemon thread, waits for `/api/health`, then opens the window. Pass `--dev` to point the window at the Vite dev server instead of the built `dist/`.
- **`backend/app.py`** — Flask app factory (`create_app`). Runs DB init, registers all blueprints, mounts auth middleware, serves the built `dist/` in production, restores the sleep inhibitor, snapshots baseline GPU VRAM for the Settings VRAM budget, and (with `STT_LISTENER=1`) spawns the voice listener as a subprocess.

### Backend Structure (`backend/`)

Flask blueprints in `backend/routes/`: `auth`, `journal`, `calendar`, `learning`, `settings`, `rag`, `chat`, `files`, `writing`, `stt`, `tasks`, `curated_tags`, `shortcuts`, `voice_command`, `transcriptions`, `cookbook`, `fanfic`, `newspapers`, `meetings`.

Feature-logic packages (kept out of the route files so they can be unit-tested):

- `backend/learning/` — FSRS scheduling adapter (`scheduler.py`), embedding dedup (`dedup.py`)
- `backend/fanfic/` — XenForo parsing/download pipeline, epub/docx import, HTML sanitizing, file storage
- `backend/meetings/` — ffmpeg recording, resumable Whisper pipeline, transcript merging, file storage
- `backend/newspapers/` — frontpages.com scraper, sync, file storage
- `backend/tags.py` — shared normalization for JSON-array tag columns (use it, don't grow per-feature rules)

The chat blueprint exposes a streaming SSE endpoint at `POST /api/chat/stream` using Flask's `Response(stream_with_context(...))`.

Long-running work (fic downloads, curated-tag scans, meeting transcription) runs in daemon threads with an in-memory progress registry; anything that must survive a restart is checkpointed to the DB, and `connection.py` resets orphaned in-flight states (`downloading` fics, `recording`/`transcribing` meetings) to `'error'` at startup.

### Database Layer (`backend/db/`)

- `schema.sql` — raw SQL `CREATE TABLE IF NOT EXISTS` statements; all IDs are ULIDs; timestamps are unix ints (converted to ISO strings by `row_to_dict`, which also camelCases column names — see `TIMESTAMP_COLS`)
- `connection.py` — opens a single WAL-mode SQLite connection (`get_db()`), runs `schema.sql` on startup, then a long list of `_ensure_*` helpers: **migrations are idempotent ALTER TABLEs guarded by `PRAGMA table_info` checks** — follow that pattern for new columns
- Three FTS5 virtual tables maintained by SQL triggers: `journal_fts`, `recipes_fts`, `fic_chapters_fts`
- `sqlite-vec` extension for vector similarity search (RAG); silently skipped if not installed
- Binary/media files live next to the DB under `./data/`: `fanfic/<fic_id>/` (images, PDFs), `meetings/<id>/` (WAV tracks), `newspapers/`, plus `shortcuts.json` (in-app key bindings). Roots overridable via `FANFIC_ROOT` / `MEETINGS_ROOT` / `NEWSPAPERS_ROOT` / `SHORTCUTS_PATH`.

### AI Layer (`backend/ai/`)

- `provider.py` — resolves the active AI provider and model from DB settings (or env vars `OPENAI_API_KEY`, `GOOGLE_API_KEY`); supports `openai`, `gemini`, `ollama`
- `llm.py` — shared provider-aware helpers: `chat_json` (JSON mode), `chat_text`, `chat_with_tools` (OpenAI-compat tool calling; raises `ToolCallingUnsupported` for gemini)
- `chat.py` — streaming chat generator consumed by the `/api/chat/stream` route
- `classifier.py` — classifies chat messages into intents: `journal | calendar | question | flashcard_request | conversation`; extracts structured data when saving entries
- `embeddings.py` — text embeddings for RAG and Learning answer-dedup; OpenAI (`text-embedding-3-small`), Gemini (`text-embedding-004`), Ollama (`nomic-embed-text`)
- `rag.py` — syncs journal entries and recipes to embeddings, semantic search, context formatting
- `commands.py` — voice-command parser (todo / calendar event / journal entry, with clarifying-question rounds)
- `journal.py` — entry polish/metadata; `classify_entry_for_tag(content, tag_name) -> bool` for the curated-tag background scan (CPU-pinned for Ollama via `_CPU_OPTIONS`)
- `learning_generation.py` / `learning_grading.py` / `learning_verification.py` — flashcard generation, claim-coverage grading, MCP-grounded verification (see Learning below)
- `mcp_client.py` — asyncio bridge to the `mcp` SDK (per-request sessions, stdio/http transports), MCP→OpenAI tool mapping
- `writing.py` — `summarize_discussion` for the Writing module
- `meetings.py` — meeting-transcript summarization (keeps the transcript tail; returns None when AI unconfigured — never fails the pipeline)
- `recipes.py` — recipe extraction from pasted text or scraped page text → `{title, content, tags}` JSON

### Auth (`backend/auth.py`)

Single-user auth via JWT cookie (`lunaschal_token`, 30-day expiry). **Auth is only enforced in network mode** (`NETWORK_MODE=1`) and only for non-localhost requests — the `check_auth` middleware in `app.py` returns early when `is_localhost(request)` is true. A matching `X-Lunaschal-Password` header also bypasses the cookie (used by the STT listener when it runs on another machine). Network mode login requires both the password and a rotating 6-digit display code (pseudo-2FA); the code is stored in the `settings` table and can be regenerated from the Settings page.

### Frontend Structure (`src/`)

- `App.tsx` — top-level view router; checks auth status on load, shows `Login` if unauthenticated in network mode, otherwise sidebar + main view + the persistent bottom `SttPanel`
- `src/components/` — one file (or subdirectory) per view: `Chat`/`ChatNav`, `Tasks`, `Journal`, `Meetings`, `Writing/`, `Calendar`, `Learning/`, `Cookbook`, `Fanfic/` (library + folders + reader), `Newspapers`, `Editor/` (file editor + STT panel), `Settings` (+ `CuratedTagsSection`, `ShortcutSettings`)
- `src/hooks/api.ts` — typed REST client (`api.*` namespaces) using plain `fetch`; no tRPC
- `src/lib/` — pure logic extracted for node-environment tests (todo sorting, tag parsing, journal feed grouping, font-size steps, fanfic helpers, VRAM thresholds…)
- `src/shortcuts/` — the in-app keyboard system (see below)
- `@` path alias resolves to `./src/`
- CSS custom properties (e.g. `var(--color-bg)`) are used for theming throughout

### In-app keyboard shortcuts (`src/shortcuts/`)

Keyboard-first, single-key navigation (the Pocket 2 has no usable mouse): WASD-style `nav.up/down/out/in`, `N` new item, `B` sidebar, plus per-view actions — all `ActionId`s and defaults in `keymap.ts`. Bindings are user-editable in Settings → Shortcuts and persisted server-side (`GET/PUT /api/shortcuts` → `data/shortcuts.json`). `ShortcutProvider` owns the global keydown listener (skipping editable targets), view cycling, and numbered **shortcut scopes** for list navigation — a scope number must be registered only once per mounted tree (last registration silently wins; the Writing nav is the canonical single-scope-2 owner). The number row is deliberately unbound for tabs — it belongs to Learning review ratings. This browser-side keymap (KeyboardEvent.code combos) is separate from the evdev key names the OS-level STT listener uses; `ShortcutProvider` maps evdev combos from settings so the listener's keys can be shown/avoided.

### Feature modules

#### Learning (`backend/routes/learning.py`, `src/components/Learning/`)

AI-augmented spaced repetition. All generated cards (brain-dump, journal, chat topic, verification follow-ups) land as `pending` in ONE approval queue (approve / steerable-regenerate / deny); scheduling is **FSRS** via the `fsrs` package (`backend/learning/scheduler.py`; `fsrs_state=NULL` = never reviewed/reset). Grading is claim-coverage: cached claim decomposition → coverage check → pre-selected Again/Hard/Good/Easy the user can override. Answer embeddings live as float32 blobs on `learning_cards` (in-Python cosine, `backend/learning/dedup.py`) powering the approve-time duplicate **hint** (never auto-reject) and the low-similarity grading gate; both silently disable without an embedding provider. Folders bind at most one MCP evidence provider (`mcp_servers` registry) for verification — trust-first: no provider/no hit ⇒ "no authoritative source found", never open-web. Revising an active answer retires the card (`revised_from` links versions, append-only `learning_revisions` log) and resets FSRS only for semantic changes. Deletes are hard deletes; FKs null `derived_from`/`revised_from` breadcrumbs.

#### Writing (`src/components/Writing/`, `backend/routes/writing.py`)

Two-panel layout: left nav (project list + a `WritingNav` with Chapters/Notes/Discussions sections) | full-width center panel that switches on the selected item: chapter → prose editor, note → note editor, discussion → chat view.

**DB tables**: `writing_projects`, `writing_chapters` (ordered by `position`), `writing_context_docs` (typed: `character | outline | worldbuilding | note`). "Notes" in the UI/API are stored in `writing_context_docs` (HTTP paths are `/api/writing/.../notes`; the table name is legacy). Discussions reuse the existing `conversations` + `messages` tables; `conversations.writing_project_id` scopes them to a project, and the general Chat tab filters them out (`writing_project_id IS NULL`). Deleting a project deletes its discussions.

**Chapter/note editors**: plain `<textarea>` (not CodeMirror — prose, not code) with 1.5 s debounced auto-save; chapters add live word count and font-size shortcuts. **Discussions**: full-size chat reusing `/api/chat/stream` unchanged; the frontend assembles a `systemPrompt` from the project plus checked notes. A **Summarize** button distills the transcript into a new note via `backend/ai/writing.py`.

#### Fanfic library (`backend/routes/fanfic.py`, `backend/fanfic/`, `src/components/Fanfic/`)

Personal fanfiction library + reader ("Library" in the UI). Imports from XenForo forums (SpaceBattles / Sufficient Velocity / Questionable Questing) by scraping threadmark reader pages — `xenforo.py` is a **pure parser** (no network/DB; tests feed fixture HTML), `download.py` streams chapters into the DB one reader page at a time (resumable; in-memory progress registry; 2 s request delay; browser UA + per-domain cookies from `site_cookies` for Cloudflare). Also imports epub/docx uploads and stores PDFs. Chapters keep sanitized HTML + plain text (FTS). Per-fic: folders (ordered), site tags, per-chapter read tracking, last-read position, rating/review, update checking (`check-updates` / `refresh-alerts` set `update_pending`). Journal entries can reference fics/chapters (`journal_entry_fic_refs`) — reading commentary shows up in the Journal feed and deep-links back into the reader.

#### Meetings (`backend/routes/meetings.py`, `backend/meetings/`, `src/components/Meetings.tsx`)

Records two PulseAudio/PipeWire streams via ffmpeg — mic + default sink `.monitor` (system audio) — one meeting at a time; optional echo-cancel via PipeWire's module in monitor mode (failure falls back to raw mic — EC must never cost a recording). Uploads are transcoded to the system track. Transcription is a background pipeline (`pipeline.py`): user picks Whisper model/device → chunked resumable transcription (checkpointed to the `meetings` row after every 30 s chunk; pausable, survives restarts) → pyannote diarization of the system track when an `hf_token` is set → `merge.py` (pure, ML-free: echo-bleed stripping, speaker labeling, coalescing) → AI summary. Progress lives in the `phase` column; `status='error'` preserves `phase` as the resume point for `/retry`.

#### Cookbook (`backend/routes/cookbook.py`, `backend/ai/recipes.py`, `src/components/Cookbook.tsx`)

Recipe collection. Paste text or a URL — the page is fetched and stripped, then `parse_recipe` extracts title/markdown-content/tags via LLM JSON mode. FTS search (`recipes_fts`), tag filtering, optional RAG embeddings.

#### Tasks & todos (`backend/routes/tasks.py`, `src/components/Tasks.tsx`)

Two lists in one view: **daily tasks** (max 4, per-day completions in `daily_task_completions`, reset each day) and one-off **todos**. The STT listener runs a **task-nudge loop**: on an interval (Settings → nudges, default 45 min, waking-hours window) it picks a pending daily task and starts a short spoken check-in conversation about it.

#### Newspapers (`backend/routes/newspapers.py`, `backend/newspapers/`)

Archives daily front pages (Toronto Star, NYT) from frontpages.com. The scraper decodes the base64-inlined image URL (the `og:image` is a decoy) and **dates editions by the date embedded in the image URL, not the local clock** — the site can serve yesterday's edition past midnight. `POST /api/newspapers/sync` is idempotent per (paper, date).

#### Transcriptions (`backend/routes/transcriptions.py`)

Append-only log of everything the STT pipeline transcribed (source/app/detail). The Journal feed can interleave them between entries (`src/lib/journalFeed.ts`; transcriptions are visible but not selectable).

### Key Behaviors

- **Curated tags** — user-defined tags managed in Settings → Tags tab. Each new tag triggers a background daemon thread that calls `classify_entry_for_tag` per journal entry and writes matches to `journal_entry_curated_tags`. Progress tracked in-memory (`_scan_progress` dict in `curated_tags.py`); the list endpoint merges it in. Tags appear as filter pill buttons in the Journal view; entries display curated tags (`#name`, neutral style) separately from freeform AI tags (accent color).
- **Journal entries** keep `raw_content` (as typed/spoken) alongside AI-polished `content`; polish, metadata generation, and embedding sync run as background threads after save. The Journal feed also interleaves fic-reading commentary via `journal_entry_fic_refs`.
- **RAG** is optional — silently disabled when embeddings aren't configured (Ollama provider, or missing API key)
- **Settings** owns more than AI keys: STT/TTS backends and Whisper model/device, voice + in-app shortcuts, curated tags, fanfic site cookies, HF token (diarization), meeting echo-cancel, task nudges, prevent-sleep (a `systemd-inhibit` subprocess), and a GPU **VRAM budget** view (baseline measured at startup, thresholds in `src/lib/vram.ts`)
- **DB path** defaults to `./data/lunaschal.db`; override with `DATABASE_URL` env var
- **JWT secret** defaults to a hardcoded dev string; set `JWT_SECRET` env var in production
- **Flask port** is always 5000; Vite dev server is 5173 and proxies `/api` to Flask (`VITE_API_PROXY_TARGET` overrides the target for split-machine dev). The Vite watcher must keep ignoring `data/**` — WAL files churn on every request and previously OOM'd the dev server.
- **Network mode**: set `NETWORK_MODE=1` and `LUNASCHAL_PASSWORD=...` to bind `0.0.0.0` and enforce auth for LAN access

A Mermaid diagram of the module structure lives in `docs/architecture.md`.

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

All four shortcuts are rebindable in Settings → Voice Shortcuts (stored in the `settings` table; env vars are fallbacks). The listener also runs the task-nudge loop (see Tasks above). Every transcription is logged to the `transcriptions` table; `POST /api/transcribe/correct` re-runs a transcript through the LLM for cleanup.

The Flask backend handles `POST /api/transcribe` and `POST /api/tts` directly (no separate port 8765 service). The Whisper model loads lazily on the first transcription request. `stt/service.py` still exists as a standalone FastAPI server but is no longer used by default.

**Local TTS**: Kokoro-ONNX (~80 MB model cached to `~/.cache/lunaschal/tts/` on first run). **API TTS**: OpenAI (`tts-1`, voice configurable via `OPENAI_TTS_VOICE`, default `nova`). Voice assistant conversation history is kept in-memory for the lifetime of the listener process. `LUNASCHAL_URL` env var overrides the chat server URL (default: `http://127.0.0.1:5000`).

STT/TTS env vars summary:

| Var                | Default | Notes                                                                 |
| ------------------ | ------- | --------------------------------------------------------------------- |
| `STT_BACKEND`      | `local` | `local` or `openai`                                                   |
| `TTS_BACKEND`      | `local` | `local` or `openai`                                                   |
| `OPENAI_API_KEY`   | —       | Required for openai backends                                          |
| `OPENAI_TTS_VOICE` | `nova`  | alloy / echo / fable / onyx / nova / shimmer                          |
| `WHISPER_MODEL`    | `turbo` | Local STT only (tiny/base/small/medium/large/large-v2/large-v3/turbo) |
| `WHISPER_DEVICE`   | `cuda`  | Local STT only (`cuda` or `cpu`)                                      |
| `STT_LISTENER`     | —       | Set to `1` to auto-start the voice listener as a subprocess of Flask  |

### Morning Check-in (`stt/morning_checkin.py`)

Daemon that monitors for wake-from-sleep events via a time-jump trick (sleeps 10 s; if the wall clock advanced >30 s, the system was suspended). When the machine wakes between `MORNING_START_HOUR` (default 8) and `MORNING_END_HOUR` (default 11), it starts a voice conversation that helps the user rubber-duck their plans for the day. A flag file in `$XDG_RUNTIME_DIR` prevents duplicate check-ins within the same calendar day.

Env vars: `STT_URL`, `LUNASCHAL_URL`, `MORNING_START_HOUR`, `MORNING_END_HOUR`.
