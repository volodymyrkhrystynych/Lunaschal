# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## STT (Speech-to-Text)

Local Whisper transcription via `faster-whisper` (`large-v3-turbo`, ~1.5 GB VRAM on CUDA).

```bash
# One-time setup (creates stt/.venv and installs Python deps)
bash stt/setup.sh

# Terminal 1 — transcription + TTS service (port 8765, downloads models on first run)
./stt/run_service.sh

# Terminal 2 — global voice input listener (runs in background)
./stt/run_listener.sh

# Terminal 3 (optional) — morning check-in daemon
./stt/run_morning_checkin.sh

# Test morning check-in immediately (skips wake detection)
./stt/run_morning_checkin.sh --now
```

Shortcuts:
- **Right Ctrl** — record → transcribe → paste text at cursor via `wtype`
- **Right Alt** — record → transcribe → AI chat (Lunaschal `/api/chat/stream`) → TTS reply spoken aloud

The Node.js server exposes `POST /api/transcribe` (multipart `audio` field) which proxies to the Python STT service. The STT service URL can be overridden with `STT_SERVICE_URL` env var (default: `http://127.0.0.1:8765`).

TTS uses **Kokoro-ONNX** (`kokoro-onnx` package, ONNX Runtime, CPU-only, ~80 MB model downloaded to `~/.cache/lunaschal/tts/` on first run). The service also exposes `POST /tts` (form field `text`). Voice assistant conversation history is kept in-memory for the lifetime of the listener process. `LUNASCHAL_URL` env var overrides the chat server URL (default: `http://127.0.0.1:3000`).

### Morning Check-in (`stt/morning_checkin.py`)

Daemon that monitors for wake-from-sleep events via a time-jump trick (sleeps 10 s; if the wall clock advanced >30 s, the system was suspended). When the machine wakes between `MORNING_START_HOUR` (default 8) and `MORNING_END_HOUR` (default 11), it starts a voice conversation that helps the user rubber-duck their plans for the day. A flag file in `$XDG_RUNTIME_DIR` prevents duplicate check-ins within the same calendar day.

Env vars: `STT_URL`, `LUNASCHAL_URL`, `MORNING_START_HOUR`, `MORNING_END_HOUR`.

## Commands

```bash
# Development (runs server on :3000 and Vite client on :5173 concurrently)
npm run dev

# Run only the backend server (tsx watch)
npm run dev:server

# Run only the frontend (Vite)
npm run dev:client

# Production build + start
npm run build
npm run start

# Database migrations
npm run db:generate   # generate migration files from schema changes
npm run db:migrate    # apply pending migrations
npm run db:studio     # open Drizzle Studio GUI
```

There are no tests or linting scripts configured.

## Architecture

Lunaschal is a single-user personal knowledge management app with AI integration. It combines a journal, calendar, flashcard system (spaced repetition), and AI chat with RAG.

### Stack
- **Frontend**: React 19 + Vite + Tailwind CSS v4 — in `src/`
- **Backend**: Hono on Node.js — in `server/`
- **API layer**: tRPC v11 with React Query — routers in `server/router/`, client hook in `src/hooks/trpc.ts`
- **Database**: SQLite via `better-sqlite3` + Drizzle ORM; stored at `./data/lunaschal.db`
- **AI**: Vercel AI SDK (`ai` package) supporting OpenAI, Google Gemini, and Ollama

### Server Structure

`server/index.ts` bootstraps Hono, runs DB migrations on startup, mounts tRPC at `/api/trpc/*`, and exposes a streaming SSE endpoint at `POST /api/chat/stream` (kept outside tRPC because tRPC doesn't support streaming responses).

tRPC routers (`server/router/`): `chat`, `journal`, `calendar`, `flashcard`, `settings`, `rag`.

### Database Layer (`server/db/`)
- `schema.ts` — Drizzle table definitions; all IDs are ULIDs
- `index.ts` — initializes SQLite, runs Drizzle migrations, then initializes FTS5 and the vector store
- `fts.ts` — SQLite FTS5 virtual table for full-text journal search
- `vectors.ts` — `sqlite-vec` extension for vector similarity search (RAG)
- Migrations are in `server/db/migrations/` and run automatically on server start

### AI Layer (`server/ai/`)
- `provider.ts` — resolves the active AI provider and model from DB settings (or env vars `OPENAI_API_KEY`, `GOOGLE_API_KEY`); supports `openai`, `gemini`, `ollama`
- `classifier.ts` — uses `generateObject` to classify chat messages into intents: `journal | calendar | question | flashcard_request | conversation`. When a message matches `journal` or `calendar`, the classifier also extracts structured data (title, tags, date, etc.) to auto-create entries.
- `embeddings.ts` — generates text embeddings for RAG; OpenAI (`text-embedding-3-small`) and Gemini (`text-embedding-004`) are supported; Ollama embeddings are not yet implemented
- `rag.ts` — syncs journal entries to embeddings, performs semantic search, formats retrieved context for the LLM
- `flashcards.ts` — AI-assisted flashcard generation
- `chat.ts` — streaming chat using the AI SDK

### Auth (`server/auth.ts`)
Single-user, password-based auth with bcrypt + JWT cookie (`lunaschal_token`, 7-day expiry). **Auth is bypassed for localhost in non-production mode** — the `requireAuth` middleware skips when `NODE_ENV !== 'production'` and the host is localhost. First-run setup flow sets the password via `settings.setupPassword` tRPC mutation.

### Frontend Structure (`src/`)
- `App.tsx` — top-level view router; renders `Setup` if no password set, otherwise shows a sidebar + main view (chat/journal/calendar/flashcards/settings)
- `src/components/` — one file per view/feature
- `@` path alias resolves to `./src/`
- CSS custom properties (e.g. `var(--color-bg)`) are used for theming throughout

### Key Behaviors
- **Flashcards** use the SM-2 spaced repetition algorithm via the `supermemo` npm package
- **RAG** is optional — silently disabled when embeddings aren't configured (Ollama provider, or missing API key)
- **DB path** defaults to `./data/lunaschal.db`; override with `DATABASE_URL` env var
- **JWT secret** defaults to a hardcoded dev string; set `JWT_SECRET` env var in production
